import os
import sys
import webbrowser
import threading
import time
import subprocess
from fidel import app, init_db, update_payment_status


def open_chrome_app():
    time.sleep(3)  # Sunucunun başlaması için bekle
    try:
        subprocess.Popen(['start', 'chrome', '--new-window', '--app=http://127.0.0.1:5000'], 
                         shell=True)
    except Exception as e:
        print(f"Chrome açılırken hata: {e}")
        print("Tarayıcıyı manuel olarak açıp http://127.0.0.1:5000 adresine gidin")

if __name__ == '__main__':
    # Veritabanını başlat
    init_db()
    update_payment_status()
    
    print("Kuaför Yönetim Sistemi başlatılıyor...")
    
    # Chrome'u uygulama modunda aç
    import threading
    threading.Thread(target=open_chrome_app).start()
    
    # Sunucuyu başlat
    app.run(debug=False, host='0.0.0.0', port=5000)
