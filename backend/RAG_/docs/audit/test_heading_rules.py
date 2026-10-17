"""快速校验 heading_rules 的判定逻辑（不加载模型、不访问向量库）。

作为 `ingestion/heading_rules.py` 的回归测试使用：覆盖语义层级识别、
伪标题过滤、页眉识别、目录识别、文书边界与噪声判定。
断言用例全部取自实测发现的真实样例，改动规则后跑一遍即可确认没有回退。
"""
import sys
from collections import Counter
from pathlib import Path

# docs/audit/ → 项目根
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wealth_rag.ingestion import heading_rules as hr  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{label}: got={got!r} want={want!r}")
    print(f"  [{'OK ' if ok else 'FAIL'}] {label:<58} -> {got!r}")


print("=== semantic_level（P0-4 语义层级） ===")
for t, want in [
    ("第一章 人民币", 1),
    ("第五章摇 反洗钱国际合作", 1),
    ("第一部分 总论", 1),
    ("第二节 人民币防伪与反假", 2),
    ("一、 人民币的特征和职能", 3),
    ("（一） 2005 年版第五套人民币100 元纸币", 4),
    ("2.1 战略资产配置", 4),
    ("2.1.1 风险", 5),
    ("1. 办理程序", 5),
    ("1 办理程序", 5),          # 乱码分隔符归一化后的形态
    ("（1） 信用卡分类", 6),
    ("第三十二条 金融机构有下列行为之一的", 7),
    ("资产配置基础", None),      # 无编号 → 交给 Markdown 层级兜底
    ("机制。", None),
]:
    check(f"semantic_level({t[:22]!r})", hr.semantic_level(t), want)

print("\n=== not_title（P0-6 伪标题过滤，实测噪声样本） ===")
for t, want in [
    ("机制。", True),
    ("防伪特征:", True),
    ("旅游购物报关单办理。", True),
    ("一、什么是资产配置?", False),        # 带问号的合法标题不得误伤
    ("第三章摇 人民币", False),
    ("第三十二条 金融机构应当", False),
    ("资产配置基础", False),
    ("个人金融信息保护法律规定", False),   # 不得被当成文书起始
    ("人民币防伪特征", False),
    ("这一行是相当长的一句中文正文内容仅仅用来触发长度阈值判断规则的实际行为表现", True),
]:
    check(f"not_title({t[:24]!r})", hr.not_title(t), want)

print("\n=== is_document_start（P0-5 文书边界） ===")
for t, want in [
    ("存款保险条例", "存款保险条例"),
    ("征信业管理条例", "征信业管理条例"),
    ("中华人民共和国反洗钱法", "中华人民共和国反洗钱法"),
    ("中华人民共和国商业银行法", "中华人民共和国商业银行法"),
    ("个人金融信息保护法律规定", None),
    ("第三章 人民币", None),
    ("第一章 总则", None),
]:
    check(f"is_document_start({t[:24]!r})", hr.is_document_start(t), want)

print("\n=== is_running_header（P0-1 页眉） ===")
doc = "金融知识普及读本"
freq = Counter({hr.norm_title("金融知识普及读本 (第二版)"): 57})
check("页眉（书名，出现57次）", hr.is_running_header("金融知识普及读本 (第二版)", doc, freq, 3), True)
check("正文标题（未重复）", hr.is_running_header("第一章 人民币", doc, freq, 3), False)
check("书名（出现1次，未达阈值）",
      hr.is_running_header("金融知识普及读本", doc, Counter({hr.norm_title("金融知识普及读本"): 1}), 3), False)

print("\n=== is_contents_title / looks_like_toc_table（P0-2 目录） ===")
check("is_contents_title('目摇 摇 录')", hr.is_contents_title("目摇 摇 录"), True)
check("is_contents_title('目 录')", hr.is_contents_title("目 录"), True)
check("is_contents_title('目录')", hr.is_contents_title("目录"), True)
check("is_contents_title('第一章 人民币')", hr.is_contents_title("第一章 人民币"), False)

toc_rows = [hr.__dict__ and [c.strip() for c in r.strip().strip("|").split("|")] for r in [
    "| 第一章摇 | 人民币 …………………………………………………………………… | 1 |",
    "| 第一节摇 | 人民币概述 ………………………………………………………… | 3 |",
    "| 第二节摇 | 人民币防伪与反假 ……………………………………………… | 14 |",
    "| 第三节摇 | 怀疑是假币时如何处理 ………………………………………… | 21 |",
]]
check("looks_like_toc_table(目录表格)", hr.looks_like_toc_table(toc_rows), True)
data_rows = [["再平衡方式", "触发条件", "优点"], ["定期再平衡", "每半年", "简单"], ["阈值再平衡", "偏离5%", "贴近目标"]]
check("looks_like_toc_table(普通数据表)", hr.looks_like_toc_table(data_rows), False)

print("\n=== is_noise_only（P1-2 噪声块） ===")
for t, want in [
    ("续表", True),
    ("66", True),
    ("<!-- image -->", True),
    ("<!-- formula-not-decoded -->", True),
    ("   ", True),
    ("期货交易的基本特征可以归纳为以下几个方面:", False),
    ("资料来源: 根据公开资料整理。", False),
]:
    check(f"is_noise_only({t[:34]!r})", hr.is_noise_only(t), want)

print("\n=== norm_title ===")
check("norm_title('金融知识普及读本 (第二版)')", hr.norm_title("金融知识普及读本 (第二版)"), "金融知识普及读本二")
check("norm_title('第一章 人民币')", hr.norm_title("第一章 人民币"), "一章人民币")

print("\n" + "=" * 70)
if fails:
    print(f"FAILED {len(fails)}:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL PASSED")
