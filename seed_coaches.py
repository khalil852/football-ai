"""初始化教练库 — 48 支世界杯参赛队主教练"""
import requests, json, re, time

with open(".streamlit/secrets.toml", "r", encoding="utf-8") as f:
    raw = f.read()
s = {}
for line in re.sub(r"=\s*'''[\s\S]*?'''", "= ''", raw).split("\n"):
    m = re.match(r'^(\w+)\s*=\s*"([^"]*)"\s*(?:#.*)?$', line.strip())
    if m:
        s[m.group(1)] = m.group(2)

url = s["SUPABASE_URL"]
key = s["SUPABASE_KEY"]
headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

# 48 队教练数据
COACHES = [
    ("Mexico", "贾维尔·阿吉雷", "墨西哥", "4-3-3", 1.05, "aggressive"),
    ("South Africa", "雨果·布罗斯", "南非", "4-4-2", 0.95, "balanced"),
    ("South Korea", "金度勋", "韩国", "4-2-3-1", 1.10, "aggressive"),
    ("Czechia", "伊万·哈谢克", "捷克", "3-4-3", 1.00, "balanced"),
    ("Canada", "杰西·马什", "加拿大", "4-3-3", 1.08, "aggressive"),
    ("Bosnia-Herzegovina", "谢尔盖·巴尔巴雷兹", "波黑", "4-4-2", 0.90, "defensive"),
    ("Qatar", "丁丁·马克斯", "卡塔尔", "4-2-3-1", 0.85, "defensive"),
    ("Switzerland", "穆拉特·雅金", "瑞士", "4-2-3-1", 0.95, "balanced"),
    ("Brazil", "多里瓦尔·儒尼奥尔", "巴西", "4-3-3", 1.15, "aggressive"),
    ("Morocco", "瓦利德·雷格拉吉", "摩洛哥", "4-3-3", 1.00, "balanced"),
    ("Haiti", "加布里埃尔·卡尔德隆", "海地", "4-4-2", 0.80, "defensive"),
    ("Scotland", "史蒂夫·克拉克", "苏格兰", "4-2-3-1", 1.00, "balanced"),
    ("USA", "格雷格·贝尔哈特", "美国", "4-3-3", 1.05, "aggressive"),
    ("Paraguay", "丹尼尔·加内罗", "巴拉圭", "4-4-2", 0.90, "defensive"),
    ("Australia", "格雷厄姆·阿诺德", "澳大利亚", "4-2-3-1", 1.00, "balanced"),
    ("Turkey", "温森佐·蒙特拉", "土耳其", "4-2-3-1", 1.05, "aggressive"),
    ("Germany", "朱利安·纳格尔斯曼", "德国", "4-2-3-1", 1.12, "aggressive"),
    ("Curacao", "德克·休斯", "库拉索", "4-4-2", 0.80, "defensive"),
    ("Ivory Coast", "埃默斯·法埃", "科特迪瓦", "4-3-3", 1.00, "balanced"),
    ("Ecuador", "费利克斯·桑切斯", "厄瓜多尔", "4-3-3", 1.00, "balanced"),
    ("Netherlands", "罗纳德·科曼", "荷兰", "4-3-3", 1.10, "aggressive"),
    ("Japan", "森保一", "日本", "4-2-3-1", 1.05, "balanced"),
    ("Sweden", "容·达尔·托马森", "瑞典", "4-3-3", 1.00, "balanced"),
    ("Tunisia", "卡德尔·克什塔", "突尼斯", "4-4-2", 0.90, "defensive"),
    ("Belgium", "多梅尼科·特德斯科", "比利时", "4-2-3-1", 1.05, "aggressive"),
    ("Egypt", "霍萨姆·哈桑", "埃及", "4-3-3", 1.00, "balanced"),
    ("Iran", "阿米尔·加莱诺伊", "伊朗", "4-4-2", 0.90, "defensive"),
    ("New Zealand", "达伦·巴泽利", "新西兰", "4-4-2", 0.85, "defensive"),
    ("Spain", "路易斯·德拉富恩特", "西班牙", "4-3-3", 1.10, "aggressive"),
    ("Cape Verde", "佩德罗·布里托", "佛得角", "4-4-2", 0.85, "defensive"),
    ("Saudi Arabia", "埃尔韦·勒纳尔", "沙特", "4-3-3", 0.95, "balanced"),
    ("Uruguay", "马塞洛·贝尔萨", "乌拉圭", "4-3-3", 1.15, "aggressive"),
    ("France", "迪迪埃·德尚", "法国", "4-2-3-1", 1.08, "aggressive"),
    ("Senegal", "阿利乌·西塞", "塞内加尔", "4-3-3", 1.00, "balanced"),
    ("Iraq", "赫苏斯·卡萨斯", "伊拉克", "4-4-2", 0.85, "defensive"),
    ("Norway", "斯托莱·索尔巴肯", "挪威", "4-3-3", 1.05, "aggressive"),
    ("Argentina", "莱昂内尔·斯卡洛尼", "阿根廷", "4-3-3", 1.10, "aggressive"),
    ("Algeria", "弗拉基米尔·佩特科维奇", "阿尔及利亚", "4-3-3", 1.00, "balanced"),
    ("Austria", "拉尔夫·朗尼克", "奥地利", "4-4-2", 1.08, "aggressive"),
    ("Jordan", "侯赛因·阿穆塔", "约旦", "4-4-2", 0.90, "defensive"),
    ("Portugal", "罗伯托·马丁内斯", "葡萄牙", "4-3-3", 1.10, "aggressive"),
    ("Congo DR", "塞巴斯蒂安·德萨布雷", "刚果", "4-4-2", 0.90, "defensive"),
    ("Uzbekistan", "斯雷奇科·卡坦内茨", "乌兹别克", "4-3-3", 0.95, "balanced"),
    ("Colombia", "内斯托尔·洛伦佐", "哥伦比亚", "4-3-3", 1.05, "aggressive"),
    ("England", "托马斯·图赫尔", "英格兰", "4-2-3-1", 1.12, "aggressive"),
    ("Croatia", "兹拉特科·达利奇", "克罗地亚", "4-3-3", 1.00, "balanced"),
    ("Ghana", "奥托·阿多", "加纳", "4-3-3", 1.00, "balanced"),
    ("Panama", "托马斯·克里斯蒂安森", "巴拿马", "4-4-2", 0.85, "defensive"),
]

count = 0
for team, name, nat, formation, aggression, style in COACHES:
    try:
        r = requests.post(
            f"{url}/rest/v1/coaches",
            headers=headers,
            json={
                "name": name,
                "team": team,
                "nationality": nat,
                "formation": formation,
                "aggression": aggression,
                "style": style,
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            count += 1
        else:
            print(f"  FAIL {team}: {r.status_code}")
    except Exception as e:
        print(f"  FAIL {team}: {e}")

print(f"\nDone. Inserted {count}/{len(COACHES)} coaches.")
