"""Verify the integrity of compressed files against source videos."""

from __future__ import annotations

from pathlib import Path

from clio.config import AppConfig
from clio.vmeta import VideoIndex, VideoMeta, _quick_hash


def run_verify(config: AppConfig) -> int:
    compressed_dir = config.compressed_dir
    if not compressed_dir.is_dir():
        print(f"压缩目录不存在: {compressed_dir}")
        return 1

    vindex_files = sorted(compressed_dir.glob("*.vindex"))
    if not vindex_files:
        print(f"没有找到 .vindex 文件（{compressed_dir}）")
        return 0

    total = len(vindex_files)
    ok_count = stale_count = missing_count = hash_fail_count = 0

    for idx, vindex_path in enumerate(vindex_files, 1):
        stem = vindex_path.stem
        print(f"[{idx}/{total}] {stem}  ", end="")

        vindex = VideoIndex.read(stem, compressed_dir)
        if vindex is None:
            print("✗ VINDEX_READ_ERROR")
            stale_count += 1
            continue

        source = Path(vindex.source_path)
        if not source.is_file():
            print(f"✗ SOURCE_MISSING ({vindex.source_path})")
            missing_count += 1
            continue

        if vindex.is_stale(source):
            print("⚠ STALE (source mtime/size changed)")
            stale_count += 1
            continue

        declared = vindex.declared_paths(compressed_dir)
        if not declared:
            print(f"✗ NO_SEGMENTS ({vindex.source_stem})")
            stale_count += 1
            continue

        all_segments_ok = True
        for seg_path in declared:
            if not seg_path.is_file():
                print(f"✗ SEGMENT_MISSING ({seg_path.name})")
                all_segments_ok = False
                missing_count += 1
                continue

            meta = VideoMeta.read(seg_path)
            if meta is None:
                print(f"✗ VMETA_MISSING ({seg_path.name})")
                all_segments_ok = False
                stale_count += 1
                break

            if meta.is_stale(source, seg_path):
                if meta.verify and _quick_hash(seg_path) != meta.verify:
                    print(f"✗ HASH_MISMATCH ({seg_path.name})")
                    hash_fail_count += 1
                else:
                    print(f"⚠ VMETA_STALE ({seg_path.name})")
                    stale_count += 1
                all_segments_ok = False
                break

        if all_segments_ok:
            print("✓ OK")
            ok_count += 1

    print(
        f"\n结果: {total} 个文件, {ok_count} OK, {stale_count} 过期, {missing_count} 缺失, {hash_fail_count} 哈希不匹配"
    )
    return 0 if ok_count == total else 1
