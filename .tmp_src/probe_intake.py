import sys
sys.path.insert(0,".")
from app.agents.intake_graph import _rule_extract
cases=["中国银行，统一社会信用代码 911000001000013428，主体评级 AAA，注册资本 2943 亿元，成立于 1983 年，机构类型 商业银行。",
       "中信证券股份有限公司 机构性质：证券公司 注册资本 100 亿元 法定代表人 张三",
       "某某农商行，评级 AA，成立于 2005年3月"]
for c in cases:
    f=_rule_extract(c)
    print({k:v for k,v in f.items() if v})
