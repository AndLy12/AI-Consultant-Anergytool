"""Production-frontend Даши + backend + ядро: прогон без dev-прокси.

БД, порт и аккаунт сотрудника одноразовые. Используем исходные браузерные
тесты Даши без правок, а настоящий API модели не вызываем ни в одном режиме.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description="Проверка собранного frontend с backend")
    parser.add_argument('--mode',choices=['stub','demo'],default='demo')
    mode=parser.parse_args().mode
    npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
    if not npm:
        raise SystemExit('Нужен Node.js с npm; сначала npm ci в frontend')
    subprocess.run([npm,'run','build'],cwd=ROOT/'frontend',check=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='energytool-fullstack-') as temporary:
        folder=Path(temporary)
        env={k:v for k,v in os.environ.items() if not k.startswith(('LLM_','OPENAI_','CORE_'))}
        env.update(DATABASE_URL='sqlite:///'+(folder/'app.db').as_posix(),
            CORE_MODE='stub' if mode=='stub' else 'real',CORE_DEMO='false' if mode=='stub' else 'true',
            APP_ORIGIN=origin,COOKIE_SECURE='false',UI_BASE_URL=origin,
            FRONTEND_DIST=str(ROOT/'frontend'/'dist'),GEN_MODEL='')
        subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,env=env,check=True)
        with (folder/'backend.log').open('w',encoding='utf-8') as logfile:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:create_app','--factory',
                '--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=logfile,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError('Тестовый backend завершился до готовности')
                    try:
                        with urllib.request.urlopen(origin+'/health',timeout=2) as response:
                            health=json.load(response)
                        assert health['frontend']=='react'
                        assert health['core_demo']==(mode=='demo')
                        break
                    except (OSError,urllib.error.URLError):
                        if time.monotonic()>=deadline:
                            raise RuntimeError('Тестовый backend не готов') from None
                        time.sleep(.2)
                # Данные заведомо вымышленные; роль выдаётся только в нашей временной БД.
                request=urllib.request.Request(origin+'/api/auth/register',
                    data=json.dumps({'email':'reviewer@example.test','password':'TestPass12345'}).encode(),
                    headers={'Content-Type':'application/json'},method='POST')
                with urllib.request.urlopen(request,timeout=10) as response:
                    assert response.status==201
                subprocess.run([sys.executable,'-m','backend.manage','grant-staff',
                    '--email','reviewer@example.test'],cwd=ROOT,env=env,check=True)
                subprocess.run([npm,'run','test:ui'],cwd=ROOT/'frontend',env=env,check=True)
                # Отдельная регрессия нашей интеграции: форма до ответа модели.
                # Тесты и исходники frontend остаются точной версией ветки Даши.
                subprocess.run([shutil.which('node'),'scripts/check_late_summary.mjs'],cwd=ROOT,env=env,check=True)
                subprocess.run([npm,'run','test:staff'],cwd=ROOT/'frontend',env=env,check=True)
                print(f'Full-stack browser checks OK: {mode}; original Dasha browser tests passed')
            except Exception:
                logfile.flush()
                print((folder/'backend.log').read_text(encoding='utf-8'))
                raise
            finally:
                # Завершаем только свой Popen; рабочий сервер пользователя не трогаем.
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__=='__main__':
    main()
