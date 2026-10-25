#!/usr/bin/env bash
# Install or update the health dashboard as an app, apart from this repo: one version from GitHub in a Python environment
# in the app folder, and the settings files on the first install. An update replaces only the environment.
# Usage: ./install.sh <version>                                    for example ./install.sh v0.1.0
#        HEALTH_DASHBOARD_HOME=/other/app/folder ./install.sh <version>
set -euo pipefail

VERSION=${1:?Give the version to install, for example: ./install.sh v0.1.0}
APP=${HEALTH_DASHBOARD_HOME:-$HOME/.local/share/md_apps/health_dashboard}
REPO=marcdumon/health_dashboard

for tool in uv npm curl; do
    command -v "$tool" >/dev/null || { echo "Missing '$tool': install it first." >&2; exit 1; }
done

mkdir -p "$APP"
[[ -d $APP/env ]] || uv venv --quiet --python 3.13 "$APP/env"
echo "Installing health_dashboard $VERSION in $APP/env ..."
uv pip install --quiet --no-sources --python "$APP/env/bin/python" --reinstall-package health-dashboard "git+https://github.com/$REPO@$VERSION"

for name in health_dashboard person; do
    if [[ ! -f $APP/$name.toml ]]; then
        curl -sfL "https://raw.githubusercontent.com/$REPO/$VERSION/$name.example.toml" -o "$APP/$name.toml"
        echo "Made $APP/$name.toml from the example: set your own values in it."
    fi
done
echo "Installed. Start the app with: $APP/env/bin/health-dashboard"
