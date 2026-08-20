"""生成 N 条假专家数据用于压测/演示（默认 10000）。用法: python scripts/seed_demo.py [N] [--db path]
全部为随机拼接的虚构信息。"""
import os, random, sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "--db" in sys.argv:
    os.environ["DB_PATH"] = sys.argv[sys.argv.index("--db") + 1]

from sqlalchemy.orm import sessionmaker  # noqa: E402
from app import history  # noqa: E402,F401
from app.models import Expert, Meeting, Participation, Tag, engine, expert_tag, init_db  # noqa: E402

N = int(next((a for a in sys.argv[1:] if a.isdigit()), 10000))
random.seed(42)
SURNAME = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华玉萍红梅鹏斌飞宇浩晨阳欣悦佳雪琳婷"
CITY = ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "苏州", "天津", "西安", "重庆", "长沙", "济南", "郑州"]
ORG_T = ["{c}大学附属第一医院", "{c}大学医学院", "{c}肿瘤医院", "{c}药科大学", "{c}市人民医院", "{c}生物医药有限公司",
         "{c}药物研究所", "{c}协和医院", "{c}中医药大学", "{c}创新药业股份有限公司"]
TITLE = ["教授", "副教授", "主任医师", "副主任医师", "研究员", "副研究员", "首席科学家", "研发副总裁", "总经理", "审评员", "主治医师", "博士"]
FIELD = ["ADC药物", "双特异性抗体", "CAR-T细胞治疗", "小分子激酶抑制剂", "PD-1/PD-L1", "mRNA疫苗", "基因治疗", "纳米递送", "PROTAC",
         "肿瘤免疫", "乳腺癌", "肺癌", "血液肿瘤", "真实世界研究", "临床药理", "药物警戒", "CMC", "生物类似药", "罕见病", "代谢性疾病",
         "神经退行性疾病", "心血管", "自身免疫病", "药物经济学", "注册申报", "GCP质量", "生物统计", "医学影像AI", "微生物组", "中药现代化"]
TAGS = ["肿瘤", "免疫治疗", "ADC", "临床研究", "监管", "工业界", "学术界", "药剂学", "小分子", "生物药", "疫苗", "基因治疗", "CMC",
        "统计", "注册", "罕见病", "神经", "心血管", "代谢", "中药", "AI", "影像", "真实世界", "药物警戒", "早期研发", "转化医学"]
MEET = ["同写意年度大会", "创新药临床开发峰会", "ADC创新论坛", "肿瘤免疫治疗研讨会", "生物药CMC论坛", "新药注册与审评论坛", "罕见病药物研发大会"]
ROLE = ["报告人", "主席", "主持", "讨论嘉宾", "", ""]

init_db()
s = sessionmaker(bind=engine)()
tags = {}
for t in TAGS:
    obj = s.query(Tag).filter_by(name=t).first() or Tag(name=t)
    s.add(obj)
    tags[t] = obj
s.flush()

# 会议：每个主题每年一场，带真实起止日期；今年之后的排成"筹备中/已确定"
CITY_MEET = ["北京", "上海", "苏州", "广州", "杭州", "成都"]
meetings = []
today = datetime.now().date()
for name in MEET:
    for yr in range(2019, today.year + 2):
        start = date(yr, random.randint(3, 11), random.randint(1, 27))
        end = start + timedelta(days=random.choice((0, 1, 2)))
        if end < today:
            st = "done"
        elif start > today + timedelta(days=120):
            st = "planned"
        else:
            st = "confirmed"
        m = Meeting(name=f"{name}", year=yr, start_date=start, end_date=end,
                    location=random.choice(CITY_MEET), status=st)
        s.add(m)
        meetings.append(m)
s.flush()
print(f"会议 {len(meetings)} 场（含 {sum(1 for m in meetings if m.start_date >= today)} 场未来会议）")

now = datetime.now()
batch = []
for i in range(N):
    name = random.choice(SURNAME) + "".join(random.choice(GIVEN) for _ in range(random.choice((1, 2))))
    city = random.choice(CITY)
    org = random.choice(ORG_T).format(c=city)
    fields = random.sample(FIELD, random.choice((1, 2, 3)))
    e = Expert(name=name, org=org, title=random.choice(TITLE), field="、".join(fields),
               phone=f"1{random.choice('3578')}{random.randint(100000000, 999999999)}",
               email=f"user{i}@example.com" if random.random() < 0.7 else "",
               bio=f"长期从事{fields[0]}相关研究，主持多项课题。", source="seed_demo",
               created_at=now - timedelta(days=random.randint(0, 900)),
               updated_at=now - timedelta(days=random.randint(0, 900)))
    e.tags = [tags[t] for t in random.sample(TAGS, random.choice((1, 2, 3, 4)))]
    for _ in range(random.choice((0, 0, 0, 1, 1, 2, 3))):
        mt = random.choice(meetings)
        e.meetings.append(Participation(meeting_id=mt.id, meeting=mt.name, year=mt.year,
                                        role=random.choice(ROLE), topic=random.choice(fields) + "进展"))
    batch.append(e)
    if len(batch) >= 500:
        s.add_all(batch)
        s.flush()
        batch.clear()
        print(f"\r{i + 1}/{N}", end="", flush=True)
s.add_all(batch)
s.commit()
print(f"\n完成：共 {s.query(Expert).count()} 位专家")
