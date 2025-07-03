import os
import sys
import webbrowser
import threading
import time
from fidel import app, init_db, update_payment_status

def open_browser():
    """5 saniye bekleyip tarayıcıyı aç"""
    time.sleep(5)
    webbrowser.open('http://127.0.0.1:5000')

if __name__ == '__main__':
    # PyInstaller için yol ayarlamaları
    if getattr(sys, 'frozen', False):
        # EXE modunda
        base_dir = sys._MEIPASS
        os.chdir(os.path.dirname(sys.executable))
    else:
        # Normal Python modunda
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Veritabanını başlat
    init_db()
    update_payment_status()
    
    # Tarayıcıyı aç
    threading.Thread(target=open_browser).start()
    
    # Uygulamayı başlat
    print("Kuaför Yönetim Sistemi başlatılıyor...")
    print("Tarayıcı otomatik açılacak...")
    print("Uygulamayı kapatmak için bu pencereyi kapatın.")
    app.run(debug=False, host='127.0.0.1', port=5000)
