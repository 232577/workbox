"""Anonymous, filesystem-free tests for migrated material-check semantics."""
import importlib
from dataclasses import asdict
from pathlib import Path

import pytest


@pytest.fixture
def engine():
    class Engine:
        def __getattr__(self, name):
            assert (Path(__file__).resolve().parents[1] / 'material_matching.py').is_file(), 'missing material matching engine'
            return getattr(importlib.import_module('material_matching'), name)
    return Engine()


def rule(e, key='manual', family='word', keywords='说明书', count=1):
    return e.Requirement(key, key, family, count, keywords)


def test_keyword_tokens_and_normalization(engine):
    e = engine
    assert e.keyword_terms(' 软件名称，代码 + ＡＰＩ API 当前软件名 ') == ('{software}', '代码', 'API')
    r = rule(e, keywords='{软件名称}，说明书+API')
    result = e.match_materials([e.MaterialFile('a', 'ＡＰＩ_说明书_示例—系统.DOC')], [r], '示例系统')
    assert result.complete and result.rows[0].file_ids == ('a',)


@pytest.mark.parametrize('suffix,family', [('.doc','word'),('.docx','word'),('.wps','word'),('.odt','word'),
    ('.PDF','pdf'),('.xlsm','sheet'),('.csv','sheet'),('.et','sheet'),('.odp','slides'),('.md','text'),
    ('.webp','image'),('.mkv','video'),('.flac','audio'),('.7z','archive')])
def test_file_families_accept_variants_without_conversion(engine,suffix,family):
    e=engine
    result=e.match_materials([e.MaterialFile('a','材料'+suffix)], [rule(e,family=family,keywords='')], '示例系统')
    assert result.complete


def test_pdf_cannot_satisfy_word_and_all_keywords_required(engine):
    e=engine
    files=[e.MaterialFile('a','系统说明书.pdf'), e.MaterialFile('b','系统代码.docx')]
    result=e.match_materials(files,[rule(e,keywords='系统 说明书')],'系统')
    assert not result.complete and result.rows[0].missing==1
    assert result.unmatched==('a','b')


def test_same_file_cannot_count_twice_and_count_is_saturated(engine):
    e=engine;f=e.MaterialFile('a','说明书.docx')
    result=e.match_materials([f,f],[rule(e,count=2)],'系统')
    assert result.rows[0].missing==1 and not result.complete
    result=e.match_materials([f,e.MaterialFile('b','新版说明书.doc')],[rule(e)],'系统')
    assert result.rows[0].matched==1 and result.rows[0].file_ids==('a','b')


def test_conflict_is_not_guessed_and_explicit_choice_consumes_once(engine):
    e=engine
    files=[e.MaterialFile('a','说明书.docx')]
    requirements=[rule(e,'broad',keywords=''),rule(e)]
    result=e.match_materials(files,requirements,'系统')
    assert not result.complete and result.ambiguous=={'a':('broad','manual')}
    assert sum(r.matched for r in result.rows)==0
    picked=e.match_materials(files,requirements,'系统',selections={'a':'manual'})
    assert not picked.ambiguous and sum(r.matched for r in picked.rows)==1
    assert not picked.complete
    assert asdict(result)==asdict(e.match_materials(files,list(reversed(requirements)),'系统'))


@pytest.mark.parametrize('choice',[{'ghost':'manual'},{'a':'ghost'},{'a':'pdf'}])
def test_invalid_or_stale_explicit_choice_is_rejected(engine,choice):
    e=engine
    with pytest.raises(ValueError):
        e.match_materials([e.MaterialFile('a','说明书.docx')],[rule(e),rule(e,'pdf','pdf')],'系统',selections=choice)


def test_inconsistent_duplicate_identity_is_rejected(engine):
    e=engine
    with pytest.raises(ValueError):
        e.match_materials([e.MaterialFile('a','说明书.docx'),e.MaterialFile('a','代码.pdf')],[rule(e)],'系统')


@pytest.mark.parametrize('field,value', [('id',''),('family','unknown'),('count',True),('count',0),('count',21),
    ('keywords','---'),('keywords','{unknown}'),('keywords','a '*13),('keywords','x'*101),('label','')])
def test_invalid_rules_do_not_silently_match(engine,field,value):
    e=engine;d=dict(id='manual',label='说明书',family='word',count=1,keywords='说明书');d[field]=value
    with pytest.raises(ValueError):
        e.match_materials([], [e.Requirement(**d)], '系统')


def test_empty_rule_set_is_unconfigured_not_complete(engine):
    e=engine
    result=e.match_materials([e.MaterialFile('a','说明书.docx')],[],'系统')
    assert not result.complete and result.status=='unconfigured'


def test_literal_special_text_is_not_regular_expression(engine):
    e=engine
    result=e.match_materials([e.MaterialFile('a','C#(API)_说明书.docx')],[rule(e,keywords='C#(API)')],'系统')
    assert result.complete
    assert not e.match_materials([e.MaterialFile('a','任意.docx')],[rule(e,keywords='.*')],'系统').complete


def test_missing_software_name_with_placeholder_is_rejected(engine):
    e=engine
    with pytest.raises(ValueError):e.match_materials([], [rule(e,keywords='{software}')], '')


def test_snapshot_ignores_missing_directories_and_historical_archives(engine):
    e=engine
    def doc(key,kind='工作文件',**extra):
        return dict(id=key,name='说明书.docx',relative_path='说明书.docx',kind=kind,exists=True,is_directory=False,**extra)
    documents=[doc('current'),doc('archive','ZIP归档'),doc('history','材料归档')]
    documents += [dict(doc('missing'),exists=False),dict(doc('folder'),is_directory=True)]
    snapshot=dict(workspace_path='registered',errors=[],documents=documents)
    result=e.check_snapshot(snapshot,[rule(e)],'系统')
    assert result.complete and result.rows[0].file_ids==('current',)
    assert result.ignored==4


@pytest.mark.parametrize('change',[{'errors':['目录不可读']},{'workspace_path':None}])
def test_partial_or_unregistered_snapshot_cannot_claim_complete(engine,change):
    e=engine
    snapshot=dict(workspace_path='registered',errors=[],documents=[dict(id='a',name='说明书.docx',
        kind='工作文件',exists=True,is_directory=False)])
    snapshot.update(change)
    result=e.check_snapshot(snapshot,[rule(e)],'系统')
    assert not result.complete and result.status=='incomplete' and result.warnings


def test_rule_roundtrip_is_strict_and_presets_are_fresh(engine):
    e=engine
    preset=e.preset_rules('五件材料')
    saved=e.encode_rules(preset)
    assert e.decode_rules(saved)==preset and len(preset)==5
    saved['rules'][0]['count']=20
    assert e.preset_rules('五件材料')[0].count==1
    with pytest.raises(ValueError):e.decode_rules({'schema':99,'rules':[]})
    with pytest.raises(ValueError):e.decode_rules({'schema':1,'rules':[{'id':'bad'}]})


def test_rule_and_file_limits_and_duplicate_rule_ids(engine):
    e=engine
    for rs,fs in [([rule(e),rule(e)],[]),([rule(e,str(i)) for i in range(41)],[]),
                  ([rule(e)],[e.MaterialFile(str(i),'说明书.docx') for i in range(10001)])]:
        with pytest.raises(ValueError):e.match_materials(fs,rs,'系统')


@pytest.mark.parametrize('family',[[],{},None])
def test_malformed_family_is_a_validation_error(engine,family):
    e=engine
    with pytest.raises(ValueError):e.match_materials([], [rule(e,family=family)], '系统')
