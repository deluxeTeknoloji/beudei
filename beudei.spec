# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['launcher.py'],  # Başlatıcı script
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('kuaför.db', '.'),
        # Diğer gerekli dosyaları buraya ekleyin
    ],
    hiddenimports=[
        'flask', 
        'flask_wtf', 
        'flask_login', 
        'werkzeug', 
        'sqlite3',
        'jinja2',
        'webbrowser'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure, 
    a.zipped_data,
    cipher=block_cipher
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Kuaför Yönetim',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Geliştirme sırasında True, son sürümde False yapabilirsiniz
    icon='static/favicon.ico' if os.path.exists('static/favicon.ico') else None,
)
