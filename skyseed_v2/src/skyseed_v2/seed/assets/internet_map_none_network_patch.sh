#!/bin/sh
set -eu

patch_compiled() {
    target=/usr/src/app/backend/bin/api/v1/main.js
    temporary="${target}.skyseed"
    awk '
        { print }
        /var withMeta = c;/ {
            found += 1
            print "                        if (withMeta.HostConfig.NetworkMode === '\''none'\'') {"
            print "                            withMeta.NetworkSettings.Networks = {};"
            print "                        }"
        }
        END { if (found != 1) exit 42 }
    ' "$target" > "$temporary"
    mv "$temporary" "$target"
}

patch_source() {
    target=/usr/src/app/backend/src/api/v1/main.ts
    temporary="${target}.skyseed"
    awk '
        { print }
        /var withMeta = c as SeedContainerInfo;/ {
            found += 1
            print "        if (withMeta.HostConfig.NetworkMode === '\''none'\'') {"
            print "            withMeta.NetworkSettings.Networks = {};"
            print "        }"
        }
        END { if (found != 1) exit 42 }
    ' "$target" > "$temporary"
    mv "$temporary" "$target"
}

patch_compiled
patch_source
