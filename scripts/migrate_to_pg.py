"""把现有 SQLite 库整库搬到 PostgreSQL。保留主键 ID 和外键关系，可重复执行（幂等）。

用法:
    python scripts/migrate_to_pg.py --pg postgresql+psycopg://user:pass@host/db [--sqlite 路径]

不传 --sqlite 时用 DB_PATH（默认 experts.db）；不传 --pg 时用环境变量 DATABASE_URL。
跑完打印每张表 源库 / 目标库 的条数对比，任何一行数量对不上会以退出码 1 结束。

幂等做法：按主键判断，目标库里已有同 ID 的行直接跳过，不覆盖也不重复插入。
所以第二次运行只会打印"跳过 N"，数据不会变。
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, func, select, text     # noqa: E402
from app import history  # noqa: E402,F401  注册 change_log / access_log
from app.models import BASE_DIR, Base, _migrate, normalize_url  # noqa: E402

BATCH = 500


def clean(v):
    """PostgreSQL 的 text 类型不接受 NUL 字节，而 PDF/Word 抽出来的正文里偶尔会带 \x00。
    SQLite 存得下，直接搬过去会报 'unsupported Unicode escape / invalid byte sequence'。"""
    if isinstance(v, str) and "\x00" in v:
        return v.replace("\x00", "")
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=os.getenv("DB_PATH", os.path.join(BASE_DIR, "experts.db")))
    ap.add_argument("--pg", default=os.getenv("DATABASE_URL", ""))
    ap.add_argument("--batch", type=int, default=BATCH)
    a = ap.parse_args()
    if not a.pg:
        sys.exit("请用 --pg 指定目标库，或设置环境变量 DATABASE_URL")
    if not os.path.exists(a.sqlite):
        sys.exit(f"找不到源库: {a.sqlite}")

    src = create_engine(f"sqlite:///{a.sqlite}")
    dst = create_engine(normalize_url(a.pg))
    print(f"源  : sqlite:///{a.sqlite}")
    print(f"目标: {dst.url.render_as_string(hide_password=True)}\n")

    Base.metadata.create_all(dst)     # 目标库建表（已存在则跳过）
    _migrate(dst)                     # 老库补新列

    src_tables = set(__import__("sqlalchemy").inspect(src).get_table_names())
    report, total_new, total_skip = [], 0, 0

    # sorted_tables 已按外键依赖拓扑排序：先 expert / tag，后 expert_tag、participation……
    for table in Base.metadata.sorted_tables:
        if table.name not in src_tables:
            report.append((table.name, 0, 0, 0, 0))
            continue
        pk = list(table.primary_key.columns)
        with src.connect() as sc:
            rows = [dict(r._mapping) for r in sc.execute(select(table))]
        with dst.connect() as dc:
            have = {tuple(r) for r in dc.execute(select(*pk))}
            before = dc.execute(select(func.count()).select_from(table)).scalar_one()

        todo = [r for r in rows if tuple(r[c.name] for c in pk) not in have]
        skipped = len(rows) - len(todo)
        if todo:
            payload = [{k: clean(v) for k, v in r.items()} for r in todo]
            with dst.begin() as dc:
                for i in range(0, len(payload), a.batch):
                    dc.execute(table.insert(), payload[i:i + a.batch])
        with dst.connect() as dc:
            after = dc.execute(select(func.count()).select_from(table)).scalar_one()
        report.append((table.name, len(rows), before, after, skipped))
        total_new += len(todo)
        total_skip += skipped
        print(f"  {table.name:<20} 源 {len(rows):>7}  写入 {len(todo):>7}  跳过(已存在) {skipped:>7}")

    # 主键序列：insert 时显式带了 id，PG 的 sequence 不会自己往前走，
    # 不重置的话应用新增第一条就会撞主键冲突。
    print("\n重置自增序列:")
    with dst.begin() as dc:
        for table in Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not str(pk[0].type).upper().startswith("INTEGER"):
                continue
            col = pk[0].name
            seq = dc.execute(text("SELECT pg_get_serial_sequence(:t, :c)"),
                             {"t": table.name, "c": col}).scalar()
            if not seq:
                continue
            dc.execute(text(f'SELECT setval(:s, COALESCE((SELECT MAX("{col}") FROM "{table.name}"), 0) + 1, false)'),
                       {"s": seq})
            print(f"  {table.name:<20} -> {seq}")

    print("\n条数对比（源 vs 目标）:")
    bad = 0
    for name, n_src, before, after, skipped in report:
        flag = "OK " if after >= n_src else "!! "
        if after < n_src:
            bad += 1
        print(f"  {flag}{name:<20} 源 {n_src:>7}   目标 {after:>7}   （迁移前目标有 {before}）")
    print(f"\n新写入 {total_new} 行，跳过已存在 {total_skip} 行。")
    if bad:
        print(f"有 {bad} 张表条数少于源库，请检查。")
        sys.exit(1)
    print("迁移完成。")


if __name__ == "__main__":
    main()
