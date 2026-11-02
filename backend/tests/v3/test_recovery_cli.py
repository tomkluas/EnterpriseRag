# 验证恢复命令和配置预检的接线；替换环境加载和数据库工厂，备份及恢复仅使用临时目录。
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.v3.cli import main
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.domain import Domain
from app.v3.models import Tenant


class RecoveryCliTests(unittest.TestCase):
    def test_production_worker_rejects_incomplete_configuration_before_database(self):
        with patch('sys.argv',['cli','--env-file','unused','worker']),patch('app.v3.cli.load_dotenv'), \
             patch('app.v3.cli.Settings.from_env',return_value=Settings(mode='production')), \
             patch('app.v3.cli.Database',side_effect=AssertionError('must not connect')),self.assertRaises(RuntimeError):
            main()

    def test_configuration_check_never_opens_database_or_echoes_secrets(self):
        output=io.StringIO()
        settings=Settings(mode='production',bootstrap_token='secret-do-not-print')
        with patch('sys.argv',['cli','--env-file','unused','check-configuration']),patch('app.v3.cli.load_dotenv'), \
             patch('app.v3.cli.Settings.from_env',return_value=settings),patch('app.v3.cli.Database',side_effect=AssertionError('must not connect')), \
             contextlib.redirect_stdout(output),self.assertRaises(SystemExit) as exited:
            main()
        self.assertEqual(exited.exception.code,1)
        self.assertIn('V3_BOOTSTRAP_TOKEN',output.getvalue())
        self.assertNotIn('secret-do-not-print',output.getvalue())

    def run_cli(self,db,settings,*args):
        output=io.StringIO()
        with patch('sys.argv',['cli','--env-file','unused',*args]),patch('app.v3.cli.load_dotenv'), \
             patch('app.v3.cli.Settings.from_env',return_value=settings),patch('app.v3.cli.Database',return_value=db), \
             contextlib.redirect_stdout(output):main()
        return output.getvalue()

    def test_cli_backup_and_restore_use_new_store_and_preserve_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source=Database('sqlite:///'+str(root/'source.db'),testing=True)
            target=Database('sqlite:///'+str(root/'target.db'),testing=True)
            try:
                source.migrate();domain=Domain(source,root/'originals');domain.bootstrap('t','admin')
                space=domain.create_space('t','admin','test')
                received=domain.receive('t','admin',space,'a.txt',b'original')
                settings=Settings(tenant_id='t',objects_dir=root/'originals')
                self.run_cli(source,settings,'backup-bundle','--output',str(root/'bundle'))
                restored=Settings(tenant_id='t',objects_dir=root/'restored')
                self.run_cli(target,restored,'restore-bundle','--input',str(root/'bundle'),'--confirm-empty-target')
                actual=Domain(target,root/'restored').read_original('t','admin',received['document_id'])
                self.assertEqual(actual,b'original')
                # Existing target must never be overwritten.
                with self.assertRaises(ValueError):
                    self.run_cli(target,restored,'restore-bundle','--input',str(root/'bundle'),'--confirm-empty-target')
            finally:source.engine.dispose();target.engine.dispose()
