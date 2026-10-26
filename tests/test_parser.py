import importlib
import importlib.util

import pytest


@pytest.fixture
def parser_module():
    assert importlib.util.find_spec("parser") is not None, "Task 1: parser.py missing"
    module = importlib.import_module("parser")
    assert hasattr(module, "parse_message"), "Task 1: parse_message missing"
    return module


@pytest.mark.parametrize("text", ["", "  \n\t ", "本批项目：\n备注：稍后发送"])
def test_empty_or_headings_do_not_become_projects(parser_module, text):
    assert parser_module.parse_message(text) == []


def test_numbered_wechat_message_ignores_explanatory_lines(parser_module):
    text = "这批做以下两个项目：\n1. 客户档案管理系统\n说明：支持资料查询。\n2、订单跟踪工具（加急）\n本周处理"
    projects = parser_module.parse_message(text)
    assert [(p.name, p.urgent) for p in projects] == [
        ("客户档案管理系统", False), ("订单跟踪工具", True)
    ]


@pytest.mark.parametrize("text", [
    "《温室能耗系统》和《个人合同归档工具》",
    "温室能耗系统；个人合同归档工具",
    "温室能耗系统\n个人合同归档工具",
    "• 温室能耗系统\n- 个人合同归档工具",
    "（一）温室能耗系统\n（二）个人合同归档工具",
])
def test_common_project_list_formats(parser_module, text):
    assert [p.name for p in parser_module.parse_message(text)] == [
        "温室能耗系统", "个人合同归档工具"
    ]


def test_mixed_numbered_and_quoted_titles_are_not_lost(parser_module):
    assert [p.name for p in parser_module.parse_message("1. 《项目甲》\n2. 项目乙")] == [
        "项目甲", "项目乙"
    ]


def test_project_label_preserves_a_single_project(parser_module):
    assert [p.name for p in parser_module.parse_message(
        "项目名称：库存工具\n需求：导入库存\n请在周末之前完成"
    )] == ["库存工具"]


def test_urgency_is_independent_and_deduplication_keeps_it(parser_module):
    projects = parser_module.parse_message("1. 客户工具\n2. 客户工具 [加急]\n3. 应急调度系统")
    assert [(p.name, p.urgent) for p in projects] == [("客户工具", True), ("应急调度系统", False)]


def test_english_names_and_versions_remain_intact(parser_module):
    assert parser_module.parse_message("1. WorkBox v1.0")[0].name == "WorkBox v1.0"


def test_unstructured_prose_does_not_create_a_project_per_sentence(parser_module):
    assert len(parser_module.parse_message("需要一个收件工具\n可以保存消息\n还需要管理ZIP")) == 1


def test_oversized_message_is_rejected_not_silently_truncated(parser_module):
    with pytest.raises(ValueError):
        parser_module.parse_message("字" * 200001)


def test_explicit_semicolons_split_titles_without_known_suffixes(parser_module):
    assert [p.name for p in parser_module.parse_message("项目甲；项目乙；WorkBox")] == [
        "项目甲", "项目乙", "WorkBox"
    ]


def test_urgency_before_a_quoted_title_is_preserved(parser_module):
    projects = parser_module.parse_message("1. 【加急】《项目甲》\n2. 《项目乙》")
    assert [(p.name, p.urgent) for p in projects] == [("项目甲", True), ("项目乙", False)]
