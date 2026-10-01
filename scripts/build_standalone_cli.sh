#!/usr/bin/env bash
# The packager has a separate tool lock; application dependencies use uv.lock.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo 'Usage: scripts/build_standalone_cli.sh <new-output-directory>' >&2
  exit 2
fi
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
output="$(realpath -m -- "$1")"
if [[ "${MINOS_STANDALONE_BUILDER:-}" != bookworm ]]; then
  if [[ -e "$output" ]]; then
    echo 'Output already exists; use a new directory.' >&2
    exit 2
  fi
  mkdir -p -- "$output"
  docker build --platform linux/amd64 --file "$root/packaging/standalone/Dockerfile" \
    --iidfile "$output/builder-image-id" "$root"
  docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
    --env HOME=/tmp --env MINOS_STANDALONE_BUILDER=bookworm \
    --volume "$output:/output" "$(cat "$output/builder-image-id")" \
    bash scripts/build_standalone_cli.sh /output/build
  exit 0
fi
if [[ -e "$output" ]]; then
  echo 'Output already exists; use a new directory.' >&2
  exit 2
fi
mkdir -p -- "$output"
builder="$root/packaging/standalone"
builder_python="${UV_PROJECT_ENVIRONMENT:-$builder/.venv}/bin/python"
uv sync --project "$builder" --locked --python 3.14.7
uv export --project "$root" --frozen --no-dev --extra reference --no-emit-project \
  --output-file "$output/application-requirements.txt" > /dev/null
uv build --project "$root" --wheel --no-sources --out-dir "$output/wheel"
uv pip install --python "$builder_python" --require-hashes \
  --requirements "$output/application-requirements.txt"
uv pip install --python "$builder_python" --no-deps "$output"/wheel/*.whl
uv pip check --python "$builder_python"
"$builder_python" -m PyInstaller --noconfirm --clean --onedir \
  --name minos --distpath "$output/dist" --workpath "$output/work" \
  --specpath "$output" --collect-data minos --additional-hooks-dir "$builder/hooks" \
  --hidden-import morfeusz2 \
  --copy-metadata minos-health --copy-metadata rapidfuzz \
  --copy-metadata morfeusz2 \
  --copy-metadata pydantic --copy-metadata pydantic-core \
  --copy-metadata annotated-types --copy-metadata typing-extensions \
  --copy-metadata typing-inspection \
  --add-data "$root/LICENSE-MIT:licenses" --add-data "$root/LICENSE-APACHE:licenses" \
  --add-data "$root/src/minos/licenses/morfeusz2.txt:licenses" \
  --add-data "$root/src/minos/licenses/sgjp.txt:licenses" \
  --add-data "/usr/share/doc/libstdc++6/copyright:licenses/libstdc++" \
  --add-data "/usr/share/doc/libgcc-s1/copyright:licenses/libgcc" \
  "$builder/minos_entry.py"
"$output/dist/minos/minos" --version
(
  cd -- "$output/dist/minos"
  find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$output/checksums.txt"
