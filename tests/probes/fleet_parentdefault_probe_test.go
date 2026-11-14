//go:build unit

// Renders every committed RestDefinition of aruba-provider-kog against this repo's own
// vendored OAS documents and counts stamped `default: {}` paths.
//
// WHY THIS EXISTS AND WHAT IT IS FOR: upstream's own no_parent_defaults_test.go proves the
// FLAG works, on a synthetic single-$ref document. Our real shape is
// `allOf: [$ref VpcPropertiesDto]` + `nullable: true` + `description`, which is a different
// eligibility question, and we have 36 definitions across 13 documents rather than one. A
// pass upstream is therefore necessary and not sufficient.
//
// THE CONTROL IS NOT OPTIONAL. A zero here is meaningless unless the same walker can report
// a stamp, so the probe re-renders one definition's spec schema through crdgen with
// NoParentDefaults UNSET and requires a stamp to appear. If the control reports zero the
// probe FAILS rather than reporting a clean fleet: "the instrument is dead" must never
// render as "nothing is stamped".
package render_test

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	definitionv1alpha1 "github.com/krateo-platformops/oasgen-provider/apis/restdefinitions/v1alpha1"
	"github.com/krateo-platformops/oasgen-provider/internal/tools/oas2jsonschema"
	"github.com/krateo-platformops/oasgen-provider/internal/tools/render"
	"github.com/krateo-platformops/plumbing/crdgen"
	"sigs.k8s.io/yaml"
)

// Mirrors scripts/providers.py CONFIGMAP_PROVIDERS, the repo's single source of truth.
var oasFileByProvider = map[string]string{
	"network":   "network-provider.json",
	"compute":   "compute-provider.json",
	"container": "container-provider.json",
	"database":  "database-provider.json",
	"storage":   "storage-provider.json",
	"security":  "security-provider.json",
	"schedule":  "schedule-provider.json",
	"baremetal": "baremetal-provider.json",
	"project":   "project.json",
	"metering":  "metering.json",
	"profile":   "profile.json",
	"compute11": "compute-provider_v1.1.json",
}

var cmRe = regexp.MustCompile(`arubacloud-([a-z0-9]+)-openapi`)

func providerOf(oasPath string) (string, error) {
	m := cmRe.FindStringSubmatch(oasPath)
	if m == nil {
		return "", fmt.Errorf("oasPath %q does not name an arubacloud-<p>-openapi ConfigMap", oasPath)
	}
	f, ok := oasFileByProvider[m[1]]
	if !ok {
		return "", fmt.Errorf("provider %q from oasPath %q is not in the mapping", m[1], oasPath)
	}
	return f, nil
}

// stampedPaths walks the CRD and records every node where "default" is an EMPTY object.
// An authored default (`default: true`, `default: "X-Probe-Token"`) is not a stamp and is
// deliberately not counted: only a materialised PARENT is the hazard.
func stampedPaths(v any, path string, out *[]string) {
	switch t := v.(type) {
	case map[string]any:
		if d, ok := t["default"]; ok {
			if m, isMap := d.(map[string]any); isMap && len(m) == 0 {
				*out = append(*out, path)
			}
		}
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			stampedPaths(t[k], path+"."+k, out)
		}
	case []any:
		for i, e := range t {
			stampedPaths(e, fmt.Sprintf("%s[%d]", path, i), out)
		}
	}
}

func TestFleetParentDefaultStamping(t *testing.T) {
	repo := os.Getenv("ARUBA_REPO")
	if repo == "" {
		t.Fatal("set ARUBA_REPO to the aruba-provider-kog checkout")
	}

	var files []string
	err := filepath.Walk(filepath.Join(repo, "restdefinitions"), func(p string, _ os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if strings.HasSuffix(p, ".yaml") || strings.HasSuffix(p, ".yml") {
			files = append(files, p)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walking restdefinitions: %v", err)
	}
	sort.Strings(files)
	if len(files) == 0 {
		t.Fatal("found 0 RestDefinitions -- the walk is broken, not the repo clean")
	}

	parsed := map[string]oas2jsonschema.OASDocument{}
	loadDoc := func(file string) (oas2jsonschema.OASDocument, error) {
		if d, ok := parsed[file]; ok {
			return d, nil
		}
		b, err := os.ReadFile(filepath.Join(repo, "openapi", file))
		if err != nil {
			return nil, err
		}
		d, err := oas2jsonschema.NewLibOASParser().Parse(b)
		if err != nil {
			return nil, err
		}
		parsed[file] = d
		return d, nil
	}

	var (
		rendered, failures, totalStamped int
		warnBuckets                      = map[string]int{}
		perKindStamped                   = map[string][]string{}
		controlSchema                    *crdgen.Options
	)

	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		j, err := yaml.YAMLToJSON(raw)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		cr := &definitionv1alpha1.RestDefinition{}
		if err := json.Unmarshal(j, cr); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		if cr.Kind != "RestDefinition" || cr.Spec.Resource.Kind == "" {
			continue
		}

		baseFile, err := providerOf(cr.Spec.OASPath)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		base, err := loadDoc(baseFile)
		if err != nil {
			t.Fatalf("%s: parsing %s: %v", f, baseFile, err)
		}

		byVerb := map[string]oas2jsonschema.OASDocument{}
		for _, vb := range cr.Spec.Resource.VerbsDescription {
			if vb.OASPath == "" {
				continue
			}
			vf, err := providerOf(vb.OASPath)
			if err != nil {
				t.Fatalf("%s verb %s: %v", f, vb.Action, err)
			}
			d, err := loadDoc(vf)
			if err != nil {
				t.Fatalf("%s verb %s: parsing %s: %v", f, vb.Action, vf, err)
			}
			byVerb[vb.Action] = d
		}

		docs := oas2jsonschema.NewDocumentSetWithOverrides(base, byVerb)
		res, err := render.CRDs(context.Background(), cr, render.TargetGVK(cr, base), docs, render.HasSecuritySchemes(base))
		if err != nil {
			failures++
			t.Logf("RENDER FAILED  %-28s %v", cr.Spec.Resource.Kind, err)
			continue
		}
		rendered++

		for _, w := range res.GenerationWarnings {
			msg := fmt.Sprintf("%v", w)
			switch {
			case strings.Contains(msg, "stamp"), strings.Contains(msg, "parent default"):
				warnBuckets["stamped-parent default:{}"]++
			case strings.Contains(msg, "excludedSpecFields"):
				warnBuckets["excludedSpecFields matches nothing"]++
			case strings.Contains(msg, "identifier"):
				warnBuckets["identifier absent from findby items"]++
			default:
				warnBuckets["other/untyped: "+msg]++
			}
		}

		for label, crd := range map[string]any{"CRD": res.CRD, "ConfigurationCRD": res.ConfigurationCRD} {
			if crd == nil || (label == "ConfigurationCRD" && res.ConfigurationCRD == nil) {
				continue
			}
			blob, err := json.Marshal(crd)
			if err != nil {
				t.Fatalf("%s: marshalling %s: %v", f, label, err)
			}
			var m any
			if err := json.Unmarshal(blob, &m); err != nil {
				t.Fatalf("%s: %v", f, err)
			}
			var found []string
			stampedPaths(m, label, &found)
			if len(found) > 0 {
				perKindStamped[cr.Spec.Resource.Kind] = append(perKindStamped[cr.Spec.Resource.Kind], found...)
				totalStamped += len(found)
			}
		}

		// THE CONTROL MUST USE AN ELIGIBLE SHAPE. crdgen stamps only a parent that is optional,
		// type object, undefaulted itself, and has a REACHABLE DEFAULTED DESCENDANT. Most of our
		// kinds have no defaulted descendant at all, so a control built from an arbitrary
		// definition reports zero for a reason that has nothing to do with the flag -- which is
		// exactly the vacuous arm this control exists to rule out. Vpc is the known-eligible
		// shape: VpcPropertiesDto.default carries `default: true`.
		if controlSchema == nil && res.CRD != nil && cr.Spec.Resource.Kind == "Vpc" {
			for _, v := range res.CRD.Spec.Versions {
				if v.Schema == nil || v.Schema.OpenAPIV3Schema == nil {
					continue
				}
				if spec, ok := v.Schema.OpenAPIV3Schema.Properties["spec"]; ok {
					b, _ := json.Marshal(spec)
					controlSchema = &crdgen.Options{
						Group: "control.example.com", Version: "v1alpha1", Kind: "Control",
						SpecSchema: b, Managed: true,
					}
				}
				break
			}
		}
	}

	t.Logf("definitions rendered        : %d", rendered)
	t.Logf("render failures             : %d", failures)
	t.Logf("documents parsed            : %d", len(parsed))
	t.Logf("stamped `default: {}` paths : %d", totalStamped)
	for kind, paths := range perKindStamped {
		t.Logf("   STAMPED %-24s %v", kind, paths)
	}
	keys := make([]string, 0, len(warnBuckets))
	for k := range warnBuckets {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		t.Logf("warning bucket %-42s %d", k, warnBuckets[k])
	}

	if rendered == 0 {
		t.Fatal("rendered 0 definitions -- the probe is broken, not the fleet clean")
	}

	// THE CONTROL. Re-render one real spec schema with NoParentDefaults UNSET. If this does not
	// stamp, the walker or the library changed and every zero above is uninterpretable.
	if controlSchema == nil {
		t.Fatal("no control schema captured from Vpc -- the fleet walk never saw the one kind " +
			"whose shape is known eligible, so the instrument cannot be shown live")
	}
	out, warns, err := crdgen.GenerateWithWarnings(*controlSchema)
	if err != nil {
		t.Fatalf("control render failed: %v (warnings %v)", err, warns)
	}
	// crdgen.GenerateWithWarnings returns a YAML manifest, not JSON -- json.Unmarshal fails on
	// the leading document separator. Parsing it as YAML is the difference between a live control
	// and a dead one, and a dead control is what makes the fleet zero above meaningless.
	var cm any
	if err := yaml.Unmarshal(out, &cm); err != nil {
		t.Fatalf("control unmarshal: %v", err)
	}
	var ctrlFound []string
	stampedPaths(cm, "control", &ctrlFound)
	t.Logf("CONTROL stamped paths (flag UNSET, must be > 0): %d", len(ctrlFound))
	if len(ctrlFound) == 0 {
		t.Fatal("CONTROL FOUND NO STAMP: with NoParentDefaults unset the same schema must stamp. " +
			"Until this control is positive, the fleet zero above means 'the instrument cannot see a " +
			"stamp', not 'the fleet is clean'.")
	}
}

// Shared by both probes so they cannot disagree about what the fleet IS.
func restDefinitionFiles(repo string) ([]string, error) {
	var files []string
	err := filepath.Walk(filepath.Join(repo, "restdefinitions"), func(p string, _ os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if strings.HasSuffix(p, ".yaml") || strings.HasSuffix(p, ".yml") {
			files = append(files, p)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(files)
	if len(files) == 0 {
		return nil, fmt.Errorf("found 0 RestDefinitions -- the walk is broken, not the repo empty")
	}
	return files, nil
}

func loadRestDefinition(t *testing.T, f string) (*definitionv1alpha1.RestDefinition, bool) {
	t.Helper()
	raw, err := os.ReadFile(f)
	if err != nil {
		t.Fatalf("%s: %v", f, err)
	}
	j, err := yaml.YAMLToJSON(raw)
	if err != nil {
		t.Fatalf("%s: %v", f, err)
	}
	cr := &definitionv1alpha1.RestDefinition{}
	if err := json.Unmarshal(j, cr); err != nil {
		t.Fatalf("%s: %v", f, err)
	}
	if cr.Kind != "RestDefinition" || cr.Spec.Resource.Kind == "" {
		return nil, false
	}
	return cr, true
}

func loadOAS(repo, file string, cache map[string]oas2jsonschema.OASDocument) (oas2jsonschema.OASDocument, error) {
	if d, ok := cache[file]; ok {
		return d, nil
	}
	b, err := os.ReadFile(filepath.Join(repo, "openapi", file))
	if err != nil {
		return nil, err
	}
	d, err := oas2jsonschema.NewLibOASParser().Parse(b)
	if err != nil {
		return nil, err
	}
	cache[file] = d
	return d, nil
}

// TestFleetAuthShape asserts, for every kind, that the generated ConfigurationCRD exposes
// EXACTLY the auth method its OAS document declares -- by EQUALITY, not membership, which
// is openstack-nova-operator-kog's rule and the load-bearing idea here:
//
//	got == ["apiKey"]     a completeness assertion
//	"apiKey" in got       passes on a CRD that ALSO carries bearer
//
// This is the OFFLINE half of scripts/assert_auth_crds.py. It covers all 36 kinds with no
// cluster, no credentials and no vendor API, because it reads the generator's own output.
// It deliberately does NOT replace the live check: the 0.54.0 break was in
// deploy/authsecretrbac.go, which provisions the Role on the Secret, and no amount of
// CRD rendering can see that. Shape here; RBAC in a cluster.
func TestFleetAuthShape(t *testing.T) {
	repo := os.Getenv("ARUBA_REPO")
	if repo == "" {
		t.Fatal("set ARUBA_REPO to the aruba-provider-kog checkout")
	}
	// Provenance travels WITH the result: openstack-nova-operator-kog lost a run to a
	// fingerprint taken by a build that could not see the `default` keyword, where "found
	// none" and "cannot see any" are the same output. Name the instrument in the output.
	t.Logf("# instrument: TestFleetAuthShape, equality on authentication.properties keys")

	files, err := restDefinitionFiles(repo)
	if err != nil {
		t.Fatalf("%v", err)
	}
	parsed := map[string]oas2jsonschema.OASDocument{}
	checked, mismatches, notChecked := 0, 0, 0

	for _, f := range files {
		cr, ok := loadRestDefinition(t, f)
		if !ok {
			continue
		}
		baseFile, err := providerOf(cr.Spec.OASPath)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		base, err := loadOAS(repo, baseFile, parsed)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}

		// The expectation is DERIVED from the same document the generator reads, never
		// hardcoded, so it cannot drift from the vendored OAS.
		want, err := declaredAuthMethod(repo, baseFile)
		if err != nil {
			t.Logf("NOT CHECKED %-26s could not read the declared scheme: %v", cr.Spec.Resource.Kind, err)
			notChecked++
			continue
		}

		// CloudServer's create resolves against the compute 1.1 document via a per-verb
		// oasPath (oasgen#227). Passing a single-document set makes its POST unfindable and
		// the render fails -- which this probe reported as NOT CHECKED rather than as a
		// mismatch, correctly, but it was still my bug and not a defect in the subject.
		byVerb := map[string]oas2jsonschema.OASDocument{}
		for _, vb := range cr.Spec.Resource.VerbsDescription {
			if vb.OASPath == "" {
				continue
			}
			vf, verr := providerOf(vb.OASPath)
			if verr != nil {
				t.Fatalf("%s verb %s: %v", f, vb.Action, verr)
			}
			d, derr := loadOAS(repo, vf, parsed)
			if derr != nil {
				t.Fatalf("%s verb %s: %v", f, vb.Action, derr)
			}
			byVerb[vb.Action] = d
		}

		res, err := render.CRDs(context.Background(), cr, render.TargetGVK(cr, base),
			oas2jsonschema.NewDocumentSetWithOverrides(base, byVerb), render.HasSecuritySchemes(base))
		if err != nil {
			t.Logf("NOT CHECKED %-26s render failed: %v", cr.Spec.Resource.Kind, err)
			notChecked++
			continue
		}
		if res.ConfigurationCRD == nil {
			t.Logf("NOT CHECKED %-26s no ConfigurationCRD generated", cr.Spec.Resource.Kind)
			notChecked++
			continue
		}

		got, err := authMethods(res.ConfigurationCRD)
		if err != nil {
			t.Logf("NOT CHECKED %-26s %v", cr.Spec.Resource.Kind, err)
			notChecked++
			continue
		}
		checked++
		if len(got) != 1 || got[0] != want {
			t.Errorf("MISMATCH %-26s declares %q, CRD exposes %v", cr.Spec.Resource.Kind, want, got)
			mismatches++
			continue
		}
		t.Logf("OK   %-26s methods=[%s]", cr.Spec.Resource.Kind, want)
	}

	t.Logf("checked: %d   mismatches: %d   NOT checked: %d", checked, mismatches, notChecked)
	if checked == 0 {
		t.Fatal("checked 0 kinds -- the probe is broken, not the fleet correct")
	}
	if notChecked > 0 {
		t.Errorf("%d kind(s) could NOT be checked; that is not the same as correct", notChecked)
	}
}

// declaredAuthMethod returns "apiKey" or "bearer" from the document's securitySchemes.
func declaredAuthMethod(repo, file string) (string, error) {
	b, err := os.ReadFile(filepath.Join(repo, "openapi", file))
	if err != nil {
		return "", err
	}
	var doc struct {
		Components struct {
			SecuritySchemes map[string]struct {
				Type   string `json:"type"`
				Scheme string `json:"scheme"`
				In     string `json:"in"`
			} `json:"securitySchemes"`
		} `json:"components"`
	}
	if err := json.Unmarshal(b, &doc); err != nil {
		return "", err
	}
	ss := doc.Components.SecuritySchemes
	if len(ss) != 1 {
		return "", fmt.Errorf("%s declares %d security schemes, expected exactly 1", file, len(ss))
	}
	for _, v := range ss {
		switch {
		case v.Type == "apiKey":
			return "apiKey", nil
		case v.Type == "http" && strings.EqualFold(v.Scheme, "bearer"):
			return "bearer", nil
		case v.Type == "http":
			return "", fmt.Errorf("%s declares http scheme %q, which this probe does not map", file, v.Scheme)
		default:
			return "", fmt.Errorf("%s declares scheme type %q, which this probe does not map", file, v.Type)
		}
	}
	return "", fmt.Errorf("unreachable")
}

func authMethods(crd any) ([]string, error) {
	blob, err := json.Marshal(crd)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(blob, &m); err != nil {
		return nil, err
	}
	spec, _ := m["spec"].(map[string]any)
	versions, _ := spec["versions"].([]any)
	if len(versions) == 0 {
		return nil, fmt.Errorf("ConfigurationCRD has no versions")
	}
	v0, _ := versions[0].(map[string]any)
	cur := v0
	for _, k := range []string{"schema", "openAPIV3Schema", "properties", "spec", "properties", "authentication", "properties"} {
		next, ok := cur[k].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("could not descend to authentication.properties (stopped at %q)", k)
		}
		cur = next
	}
	out := make([]string, 0, len(cur))
	for k := range cur {
		out = append(out, k)
	}
	sort.Strings(out)
	return out, nil
}

// TestFleetParentDefaultBlastRadius counts how many eligible parents the fleet HAS, which is a
// different question from whether the suppression works.
//
// WHY BOTH QUESTIONS EXIST. TestFleetParentDefaultStamping reports 0 stamped paths with
// NoParentDefaults set, and its control re-renders the real Vpc spec schema with the flag UNSET
// and gets 1 -- so the eligible shape is present and the 0 is the suppression working, not the
// shape's absence. That is CORRECTNESS, and it is settled. What it does not say is across how
// many shapes the suppression has been exercised. That matters because the stamp is
// IRREVERSIBLE once a CR has been read through it: the apiserver applies the default on reads
// from etcd, so an existing CR shows a field its author never set. If the toggle ever regresses
// -- a plumbing bump, a default change, a flag dropped in a refactor -- the damage is
// proportional to N. Knowing N turns "a risk exists" into "a risk affecting N kinds".
//
// HOW IT COUNTS, which is the only sound part of the design: it re-renders each definition's
// ALREADY-RENDERED spec schema through crdgen with the flag unset, and counts the stamps crdgen
// itself produces. It does not reimplement crdgen's eligibility rules.
//
// A STATIC WALK OVER THE OAS DOCUMENTS CANNOT DO THIS, and I tried. Counting optional object
// properties with a defaulted descendant straight from the vendored documents returned 0
// eligible parents across all 36 -- a number contradicted by Vpc, which the control proves
// eligible. The cause: that walk required an explicit `type: object` on the parent, and these
// documents omit `type` on a $ref'd object while the renderer supplies it. A second instrument
// that almost agrees is worse than one honest gap, because the agreement is what gets cited.
//
// THE CONTROL IS A SUPERSET, DELIBERATELY. Vpc is known eligible, so this test's total MUST be
// >= 1 and Vpc's own count MUST be >= 1. A zero here is therefore arithmetically impossible
// rather than merely surprising -- which is the only reason the static walk's false 0 was
// caught at all.
func TestFleetParentDefaultBlastRadius(t *testing.T) {
	repo := os.Getenv("ARUBA_REPO")
	if repo == "" {
		t.Fatal("set ARUBA_REPO to the aruba-provider-kog checkout")
	}
	t.Logf("# instrument: TestFleetParentDefaultBlastRadius, stamps with NoParentDefaults UNSET")

	files, err := restDefinitionFiles(repo)
	if err != nil {
		t.Fatalf("%v", err)
	}
	if len(files) == 0 {
		t.Fatal("found 0 RestDefinitions -- the walk is broken, not the repo clean")
	}
	parsed := map[string]oas2jsonschema.OASDocument{}
	perKind := map[string]int{}
	examined, notChecked, total := 0, 0, 0

	for _, f := range files {
		cr, ok := loadRestDefinition(t, f)
		if !ok {
			continue
		}
		baseFile, err := providerOf(cr.Spec.OASPath)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		base, err := loadOAS(repo, baseFile, parsed)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		byVerb := map[string]oas2jsonschema.OASDocument{}
		for _, vb := range cr.Spec.Resource.VerbsDescription {
			if vb.OASPath == "" {
				continue
			}
			vf, verr := providerOf(vb.OASPath)
			if verr != nil {
				t.Fatalf("%s verb %s: %v", f, vb.Action, verr)
			}
			d, derr := loadOAS(repo, vf, parsed)
			if derr != nil {
				t.Fatalf("%s verb %s: %v", f, vb.Action, derr)
			}
			byVerb[vb.Action] = d
		}

		res, err := render.CRDs(context.Background(), cr, render.TargetGVK(cr, base),
			oas2jsonschema.NewDocumentSetWithOverrides(base, byVerb), render.HasSecuritySchemes(base))
		if err != nil {
			t.Logf("NOT CHECKED %-26s render failed: %v", cr.Spec.Resource.Kind, err)
			notChecked++
			continue
		}
		if res.CRD == nil {
			t.Logf("NOT CHECKED %-26s render produced no CRD", cr.Spec.Resource.Kind)
			notChecked++
			continue
		}

		counted := false
		for _, v := range res.CRD.Spec.Versions {
			if v.Schema == nil || v.Schema.OpenAPIV3Schema == nil {
				continue
			}
			spec, found := v.Schema.OpenAPIV3Schema.Properties["spec"]
			if !found {
				break
			}
			b, merr := json.Marshal(spec)
			if merr != nil {
				t.Logf("NOT CHECKED %-26s marshalling spec: %v", cr.Spec.Resource.Kind, merr)
				break
			}
			out, warns, gerr := crdgen.GenerateWithWarnings(crdgen.Options{
				Group: "blast.example.com", Version: "v1alpha1", Kind: "Blast",
				SpecSchema: b, Managed: true,
			})
			if gerr != nil {
				t.Logf("NOT CHECKED %-26s re-render failed: %v (warnings %v)", cr.Spec.Resource.Kind, gerr, warns)
				break
			}
			// YAML, not JSON: GenerateWithWarnings returns a manifest with a leading document
			// separator, and json.Unmarshal failing here is what once made a dead control read
			// as a clean fleet.
			var cm any
			if yerr := yaml.Unmarshal(out, &cm); yerr != nil {
				t.Logf("NOT CHECKED %-26s unmarshal: %v", cr.Spec.Resource.Kind, yerr)
				break
			}
			var stamps []string
			stampedPaths(cm, cr.Spec.Resource.Kind, &stamps)
			if len(stamps) > 0 {
				perKind[cr.Spec.Resource.Kind] = len(stamps)
				total += len(stamps)
			}
			counted = true
			break
		}
		if counted {
			examined++
		} else {
			notChecked++
		}
	}

	t.Logf("definitions examined              : %d", examined)
	t.Logf("not checked                       : %d", notChecked)
	t.Logf("kinds with >= 1 eligible parent   : %d", len(perKind))
	for _, k := range sortedKeys(perKind) {
		t.Logf("    %-26s %d", k, perKind[k])
	}
	t.Logf("TOTAL eligible parents (blast radius): %d", total)

	if total == 0 {
		t.Fatal("CONTROL FAILED: 0 eligible parents fleet-wide, yet Vpc is known eligible " +
			"(VpcPropertiesDto.default carries `default: true`). The instrument cannot see a stamp, " +
			"so this zero means 'cannot see' and not 'none present'.")
	}
	if perKind["Vpc"] == 0 {
		t.Fatal("CONTROL FAILED: Vpc reports 0 eligible parents. It is the known-eligible shape, " +
			"so any fleet count taken by this instrument is uninterpretable.")
	}
}

func sortedKeys(m map[string]int) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
