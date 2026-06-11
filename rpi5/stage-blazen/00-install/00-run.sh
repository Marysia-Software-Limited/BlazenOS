#!/bin/bash -e
# stage-blazen/00-install/00-run.sh — host-side prelude that copies our
# substage `files/` tree into the rootfs BEFORE the chroot script runs.
#
# Pi-gen does NOT export SUB_STAGE_DIR / ROOTFS_DIR to subprocess
# scripts, but it DOES `pushd "${SUB_STAGE_DIR}"` before running them,
# and it exports WORK_DIR + STAGE. So:
#   - $PWD               == SUB_STAGE_DIR
#   - ${WORK_DIR}/${STAGE}/rootfs == ROOTFS_DIR
#
# Also see docs/10-ROADMAP.md §"M1 operational footguns".

SUB_STAGE_DIR="$PWD"
ROOTFS_DIR="${WORK_DIR}/${STAGE}/rootfs"

if [ ! -d "${SUB_STAGE_DIR}/files" ]; then
    echo "[00-run.sh] no files/ to rsync — nothing to do"
    exit 0
fi

echo "[00-run.sh] rsyncing ${SUB_STAGE_DIR}/files/ → ${ROOTFS_DIR}/"
install -v -d "${ROOTFS_DIR}"
rsync -aHAXxh "${SUB_STAGE_DIR}/files/" "${ROOTFS_DIR}/"

# Sanity check.
if [ -d "${ROOTFS_DIR}/var/lib/blazen-staging" ]; then
    echo "[00-run.sh] OK — /var/lib/blazen-staging in rootfs:"
    ls "${ROOTFS_DIR}/var/lib/blazen-staging/" | sed 's/^/    /'
fi
if [ -d "${ROOTFS_DIR}/etc/systemd/system" ]; then
    units=$(find "${ROOTFS_DIR}/etc/systemd/system" -maxdepth 1 -name 'blazend-*' -o -name 'blazend.target' | wc -l)
    echo "[00-run.sh] OK — ${units} blazend systemd units in rootfs"
fi
