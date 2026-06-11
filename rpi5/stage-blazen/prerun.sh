#!/bin/bash -e
# stage-blazen/prerun.sh — inherit the rootfs from the previous stage
# (stage2). Pi-gen calls copy_previous to copy stage2's rootfs as our
# starting point.

if [ ! -d "${ROOTFS_DIR}" ]; then
    copy_previous
fi
