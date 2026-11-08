#!/bin/bash
# Compiles the gui-v2 UI plugin into gui-v2/BmsSwitcher/BmsSwitcher.json.
# Run on a development machine (needs python3 plus Qt's lupdate, lrelease and rcc), then commit the JSON.
# The device only needs the compiled JSON; install.sh deploys it to /data/apps/enabled/.
#
# The page uses the gui-v2 v1.3.x plugin API (Victron.VenusOS Page, Venus OS v3.8x).
# The upcoming stable Victron.GuiPlugins API refuses Page-based plugins, so cap the max version
# once that lands in a release.

set -e

GUI_V2_TAG="${GUI_V2_TAG:-v1.3.27}"
MIN_REQUIRED_VERSION="v1.3.6"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PLUGIN_DIR="$SCRIPT_DIR/BmsSwitcher"
COMPILER="${GUI_V2_COMPILER:-$SCRIPT_DIR/.gui-v2-plugin-compiler-$GUI_V2_TAG.py}"

if [ ! -f "$COMPILER" ]; then
    echo "Fetching gui-v2 plugin compiler ($GUI_V2_TAG)..."
    curl -fsSL -o "$COMPILER" \
        "https://raw.githubusercontent.com/victronenergy/gui-v2/$GUI_V2_TAG/tools/gui-v2-plugin-compiler.py"
fi

cd "$PLUGIN_DIR"
python3 "$COMPILER" \
    --name BmsSwitcher \
    --min-required-version "$MIN_REQUIRED_VERSION" \
    --settings BmsSwitcher_PageSettings.qml

# Remove build intermediates; only the .json is deployed.
rm -f BmsSwitcher.qrc BmsSwitcher.rcc ./*.qm ./*.ts

echo "Built $PLUGIN_DIR/BmsSwitcher.json"
