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
