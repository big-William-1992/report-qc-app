#!/usr/bin/env python3
"""
多机样本库合并工具（零服务器成本的多机器数据聚合）

把多台电脑各自的 samples.db 汇总进一个中心分析库，按 (ts, report_text) 去重。

用法：
  # 把指定目录下的所有 .db 合并到 merged.db
  python tools/merge_dbs.py --target merged.db --dir ./exports

  # 显式列出多个库
  python tools/merge_dbs.py --target merged.db machineA.db machineB.db machineC.db

  # 把当前软件样本库（默认 %APPDATA%/MedicalReportQC/samples.db）也并入分析库
  python tools/merge_dbs.py --target analysis.db --self

用途：内测期不做中心服务器时，各机器用软件「导出样本库」或直接使用本机
samples.db，再由管理员在本工具里一键合并，得到全院/科室级质控统计。
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import samplelib  # noqa: E402


def find_dbs(directory: str):
    """返回目录下所有 .db 文件（递归一层），跳过非样本库（accounts.db 等）。"""
    out = []
    for pat in (os.path.join(directory, "*.db"),
                os.path.join(directory, "**", "*.db")):
        for p in glob.glob(pat, recursive=True):
            if os.path.basename(p).lower() in ("accounts.db", "license.db", "session.db"):
                continue
            out.append(p)
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def main():
    ap = argparse.ArgumentParser(description="合并多机 samples.db 到中心分析库")
    ap.add_argument("dbs", nargs="*", help="要合并的源 .db 文件（可多个）")
    ap.add_argument("--target", required=True, help="合并目标库路径")
    ap.add_argument("--dir", help="扫描该目录下所有 .db 作为源")
    ap.add_argument("--self", action="store_true",
                    help="额外把当前软件样本库（db_path()）也并入")
    args = ap.parse_args()

    sources = list(args.dbs)
    if args.dir:
        sources += find_dbs(args.dir)
    if args.self:
        sources.append(samplelib.db_path())

    if not sources:
        ap.error("未指定任何源库：请用位置参数、--dir 或 --self")

    target_abs = os.path.abspath(args.target)
    total_ins = total_skip = 0
    print(f"合并目标：{args.target}\n")
    for i, src in enumerate(sources, 1):
        if not os.path.exists(src):
            print(f"  [{i}] 跳过（不存在）：{src}")
            continue
        if os.path.abspath(src) == target_abs:
            continue  # 不自我合并
        ins, skip = samplelib.merge_from_db(src, target=args.target)
        total_ins += ins
        total_skip += skip
        print(f"  [{i}] {os.path.basename(src)}: 新增 {ins} 条，跳过重复 {skip} 条")

    print(f"\n合并完成 → {args.target}")
    print(f"  累计新增 {total_ins} 条，跳过重复 {total_skip} 条")


if __name__ == "__main__":
    main()
