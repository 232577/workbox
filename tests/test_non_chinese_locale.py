"""Exercise Chinese intake labels on an English Windows locale in child processes."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.skipif(sys.platform!='win32',reason='Windows locale regression')
@pytest.mark.parametrize('entry',['text','file'])
def test_chinese_intake_labels_do_not_depend_on_windows_locale(tmp_path,entry):
    code=textwrap.dedent(r'''
        import locale,re,sys
        from pathlib import Path
        locale.setlocale(locale.LC_ALL,'English_United States.1252')
        root=Path(sys.argv[1]);entry=sys.argv[2]
        from desktop_service import DesktopService
        service=DesktopService(root/'data/workbox.db')
        if entry=='text':
            result=service.receive_text('1. 区域设置验证项目')
            assert result['state']=='committed',result
            suffix='收件'
        else:
            source=root/'区域设置验证项目_操作手册.txt'
            source.write_text('isolated test',encoding='utf-8')
            service.receive_path(source)
            assert source.read_text(encoding='utf-8')=='isolated test'
            suffix='文件收件'
        assert service.db.counts()['projects']==1
        label=service.list_batches()[0]['name']
        assert re.fullmatch(r'\d{2}月\d{2}日 \d{2}:\d{2} '+suffix+r' #\d+',label),label
        assert locale.setlocale(locale.LC_TIME).startswith('English_United States')
        print('locale-safe')
    ''')
    env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',QT_QPA_PLATFORM='offscreen')
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path),entry],cwd=ROOT,env=env,
                          capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
    assert 'locale-safe' in result.stdout
