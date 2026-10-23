"""Add `replace` directives that point vanity Go module paths at their GitHub
source repositories (the module proxy and vanity hosts are unreachable here)."""
import re, subprocess, sys

RULES = [
    (r"^golang\.org/x/([^/]+)(.*)$", r"github.com/golang/\1\2"),
    (r"^google\.golang\.org/grpc(.*)$", r"github.com/grpc/grpc-go\1"),
    (r"^google\.golang\.org/protobuf$", r"github.com/protocolbuffers/protobuf-go"),
    (r"^google\.golang\.org/genproto(.*)$", r"github.com/googleapis/go-genproto\1"),
    (r"^google\.golang\.org/api$", r"github.com/googleapis/google-api-go-client"),
    (r"^cloud\.google\.com/go/alloydbconn$", r"github.com/GoogleCloudPlatform/alloydb-go-connector"),
    (r"^cloud\.google\.com/go/cloudsqlconn$", r"github.com/GoogleCloudPlatform/cloud-sql-go-connector"),
    (r"^cloud\.google\.com/go(.*)$", r"github.com/googleapis/google-cloud-go\1"),
    (r"^go\.opentelemetry\.io/otel(.*)$", r"github.com/open-telemetry/opentelemetry-go\1"),
    (r"^go\.opentelemetry\.io/contrib(.*)$", r"github.com/open-telemetry/opentelemetry-go-contrib\1"),
    (r"^go\.opentelemetry\.io/proto/otlp$", r"github.com/open-telemetry/opentelemetry-proto-go/otlp"),
    (r"^go\.opentelemetry\.io/auto/sdk$", r"github.com/open-telemetry/opentelemetry-go-instrumentation/sdk"),
    (r"^gopkg\.in/yaml\.v3$", r"github.com/go-yaml/yaml/v3"),
    (r"^go\.yaml\.in/yaml/v(\d+)$", r"github.com/yaml/go-yaml/v\1"),
    (r"^go\.uber\.org/([^/]+)$", r"github.com/uber-go/\1"),
    (r"^go\.opencensus\.io$", r"github.com/census-instrumentation/opencensus-go"),
    (r"^cel\.dev/expr$", r"github.com/google/cel-spec"),
]

def target(path):
    for pat, rep in RULES:
        if re.match(pat, path):
            return re.sub(pat, rep, path)
    return None

def main(gomod, extra=()):
    text = open(gomod).read()
    text = re.sub(r"\n// gh-replace begin.*?// gh-replace end\n", "\n", text, flags=re.S)
    reqs = re.findall(r"^\s*([a-z0-9.\-]+\.[a-z]+/[^\s]+)\s+(v[^\s]+)", text, re.M)
    reqs += [tuple(x.split("@")) for x in extra]
    lines = []
    seen = set()
    for p, v in reqs:
        t = target(p)
        if t and p not in seen:
            seen.add(p)
            lines.append(f"replace {p} => {t} {v}")
    text += "\n// gh-replace begin\n" + "\n".join(lines) + "\n// gh-replace end\n"
    open(gomod, "w").write(text)
    print(f"{gomod}: {len(lines)} replacements")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
