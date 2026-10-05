import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if os.path.exists("redscarf.db"):
    os.remove("redscarf.db")

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
print("=" * 60)
print("红领巾讲解员 - 培训与考核精细化管理 API 测试")
print("=" * 60)

print("\n[1/10] 根路径 (版本信息)...")
r = client.get("/")
assert r.status_code == 200, f"失败: {r.status_code}"
data = r.json()
print(f"  ✅ 版本 {data['version']}, API组: {len(data['api_groups'])}个")

print("\n[2/10] 培训期次列表...")
r = client.get("/api/trainings/batches")
assert r.status_code == 200
batches = r.json()
print(f"  ✅ {len(batches)}期培训")
for b in batches:
    print(f"    - {b['name']} [{b['status']}] 容量{b['capacity']}")
batch_id = batches[0]["id"]

print("\n[3/10] 培训期次详情(课次+报名数)...")
r = client.get(f"/api/trainings/batches/{batch_id}")
assert r.status_code == 200
detail = r.json()
print(f"  ✅ {detail['name']}")
print(f"    课次: {len(detail['sessions'])}节  入班: {detail['enrollment_count']}人")
for s in detail["sessions"]:
    print(f"    #{s['session_no']} {s['title']} @{s['session_date']}")

print("\n[4/10] 考核主题列表...")
r = client.get("/api/assessments/topics")
assert r.status_code == 200
topics = r.json()
print(f"  ✅ {len(topics)}个考核主题")
for t in topics:
    print(f"    - {t['name']} (及格线{t['pass_score']})")
topic_id = topics[0]["id"]

print("\n[5/10] 主题评分项 & 题库...")
r = client.get(f"/api/assessments/topics/{topic_id}/criteria")
assert r.status_code == 200
criteria = r.json()
print(f"  ✅ {len(criteria)}项评分标准")
for c in criteria:
    print(f"    - {c['name']} 满分{c['max_score']}")
r = client.get(f"/api/assessments/topics/{topic_id}/questions")
assert r.status_code == 200
qs = r.json()
print(f"  ✅ {len(qs)}道考核题目")

print("\n[6/10] 期次报名入班 + 出勤率校验...")
r = client.get(f"/api/trainings/batches/{batch_id}/enrollments")
assert r.status_code == 200
enrolls = r.json()
print(f"  ✅ {len(enrolls)}人报名")
for e in enrolls:
    rate = e["attendance_rate"]
    ok = "✅达标" if e["eligible_for_assessment"] else "❌未达标"
    vname = e.get("volunteer", {}).get("name", "?") if e.get("volunteer") else "?"
    print(f"    - {vname}: {e['attendance_count']}/{e['total_sessions']} = {rate}% {ok}")

print("\n[7/10] 创建新培训期次（POST）...")
r = client.post("/api/trainings/batches", json={
    "name": "2026暑期讲解员培训班(测试期次)",
    "topic_id": topic_id,
    "description": "API测试创建",
    "min_attendance_rate": 70.0,
    "capacity": 15,
    "start_date": "2026-07-01",
    "end_date": "2026-07-20"
})
assert r.status_code == 200, f"失败: {r.status_code} {r.text}"
nb = r.json()
print(f"  ✅ 创建成功: #{nb['id']} {nb['name']} 状态={nb['status']}")

print("\n[8/10] 培训期次统计(出勤率 + 一次通过率)...")
r = client.get("/api/stats/training-batches")
assert r.status_code == 200
stats = r.json()
print(f"  ✅ {len(stats)}期统计")
for s in stats:
    print(f"    - {s['batch_name']}:")
    print(f"       出勤率={s['attendance_rate']}%  一次通过率={s['first_time_pass_rate']}%")
    print(f"       入班{s['enrollment_count']}人 达标{s['eligible_count']}人 考核{s['assessment_count']}次")

print("\n[9/10] 考核主题统计(一次通过率/总通过率)...")
r = client.get("/api/stats/assessment-topics")
assert r.status_code == 200
astats = r.json()
print(f"  ✅ {len(astats)}主题统计")
for s in astats[:4]:
    ft = s["first_time_pass_rate"]
    tp = s["total_pass_rate"]
    print(f"    - {s['topic_name']}: 初考{s['first_time_count']}次通过率{ft}%  补考{s['retake_count']}次 总通过率{tp}%")

print("\n[10/10] 志愿者资格证查询...")
r = client.get("/api/volunteers")
assert r.status_code == 200
vols = r.json()
certified = [v for v in vols if v["status"] == "已持证"][:2]
for v in certified:
    vid = v["id"]
    r2 = client.get(f"/api/assessments/volunteers/{vid}/certifications")
    assert r2.status_code == 200
    certs = r2.json()
    cert_names = [c["certificate_no"] for c in certs]
    print(f"  ✅ {v['name']}: {len(certs)}张资格证 {cert_names}")

print("\n[11/18] 权益商品列表...")
r = client.get("/api/benefits/")
assert r.status_code == 200
benefits = r.json()
print(f"  ✅ {len(benefits)}个权益商品")
for b in benefits[:5]:
    print(f"    - {b['name']} [{b['benefit_type']}] {b['points_cost']}积分 库存{b['stock']}")
badge_id = [b["id"] for b in benefits if b["benefit_type"] == "纪念徽章"][0]
priority_id = [b["id"] for b in benefits if b["benefit_type"] == "优先认领时段"][0]

print("\n[12/18] 获取持证志愿者（用于积分和兑换测试）...")
r = client.get("/api/volunteers")
assert r.status_code == 200
vols = r.json()
certified_vols = [v for v in vols if v["status"] == "已持证"]
test_vol = certified_vols[0]
print(f"  ✅ 选择测试志愿者: {test_vol['name']} (ID:{test_vol['id']})")

print("\n[13/18] 查询志愿者积分...")
r = client.get(f"/api/points/volunteer/{test_vol['id']}")
assert r.status_code == 200
points_data = r.json()
print(f"  ✅ 积分余额: {points_data['points_balance']}, 累计获得: {points_data['total_earned']}, 累计消耗: {points_data['total_spent']}")

print("\n[14/18] 积分统计...")
r = client.get("/api/stats/points")
assert r.status_code == 200
points_stats = r.json()
print(f"  ✅ 总发放积分: {points_stats['total_points_earned']}, 总消耗: {points_stats['total_points_spent']}, 净积分: {points_stats['net_points']}")

print("\n[15/18] 兑换权益商品...")
if points_data["points_balance"] >= 30:
    r = client.post("/api/benefits/exchanges", json={
        "volunteer_id": test_vol["id"],
        "benefit_id": [b["id"] for b in benefits if b["benefit_type"] == "其他权益"][0],
        "quantity": 1,
        "delivery_info": "测试兑换",
        "notes": "API测试"
    })
    assert r.status_code == 200, f"失败: {r.status_code} {r.text}"
    exchange = r.json()
    print(f"  ✅ 兑换成功: #{exchange['id']} 花费{exchange['points_spent']}积分 状态={exchange['status']}")

    r = client.get(f"/api/points/volunteer/{test_vol['id']}")
    new_balance = r.json()["points_balance"]
    print(f"  ✅ 兑换后积分余额: {new_balance}")
else:
    print(f"  ⏭️  跳过（积分不足）")

print("\n[16/18] 兑换统计...")
r = client.get("/api/stats/exchanges")
assert r.status_code == 200
exchange_stats = r.json()
print(f"  ✅ 总兑换: {exchange_stats['total_exchanges']}次, 待处理: {exchange_stats['pending_exchanges']}, 已完成: {exchange_stats['completed_exchanges']}")
print(f"     已取消: {exchange_stats['cancelled_exchanges']}, 总消耗积分: {exchange_stats['total_points_spent']}")

print("\n[17/18] 家长入口登录...")
r = client.post("/api/parents/login", json={
    "parent_phone": test_vol["parent_phone"],
    "volunteer_name": test_vol["name"]
})
assert r.status_code == 200, f"失败: {r.status_code} {r.text}"
login_data = r.json()
print(f"  ✅ 找到 {len(login_data['volunteers'])} 个孩子")
for v in login_data["volunteers"]:
    print(f"    - {v['name']} ({v['school_name']} {v['grade']})")

print("\n[18/18] 家长查看孩子全景数据...")
r = client.get(f"/api/parents/volunteer/{test_vol['id']}?parent_phone={test_vol['parent_phone']}")
assert r.status_code == 200, f"失败: {r.status_code} {r.text}"
parent_view = r.json()
v = parent_view["volunteer"]
print(f"  ✅ 孩子信息: {v['name']} 状态={v['status']} 星级={v['star_level_name']}")
print(f"     服务时长: {v['total_service_hours']}小时  积分余额: {v['points_balance']}")
print(f"     服务记录: {len(parent_view['service_records'])}条  培训记录: {len(parent_view['training_records'])}条")
print(f"     星级证书: {len(parent_view['certificates'])}张  积分记录: {len(parent_view['points_records'])}条  兑换记录: {len(parent_view['exchanges'])}条")

print("\n" + "=" * 60)
print("🎉 全部 18 项 API 测试通过！")
print("=" * 60)
