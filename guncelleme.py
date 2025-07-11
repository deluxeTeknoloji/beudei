import os
import sys
import json
import time
import shutil
import zipfile
import tempfile
import logging
import requests
import subprocess
from datetime import datetime

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('guncelleme.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Uygulama sürüm bilgisi
SURUM = "1.0.0"
GUNCELLEME_URL = "https://example.com/api/guncelleme"  # Güncelleme sunucusu URL'si

def surum_kontrol():
    """Sunucudan yeni sürüm kontrolü yapar"""
    try:
        logger.info("Güncelleme kontrolü yapılıyor...")
        response = requests.get(f"{GUNCELLEME_URL}/kontrol", params={"surum": SURUM}, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("guncelleme_var", False):
                logger.info(f"Yeni sürüm bulundu: {data.get('yeni_surum')}")
                return data
            else:
                logger.info("Uygulama güncel.")
                return None
        else:
            logger.warning(f"Sunucudan hata kodu: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Güncelleme kontrolü sırasında hata: {str(e)}")
        return None

def guncelleme_indir(guncelleme_bilgisi):
    """Güncellemeyi indirir ve uygular"""
    try:
        indirme_url = guncelleme_bilgisi.get("indirme_url")
        if not indirme_url:
            logger.error("İndirme URL'si bulunamadı")
            return False
        
        # Geçici dizin oluştur
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, "guncelleme.zip")
        
        # Güncellemeyi indir
        logger.info(f"Güncelleme indiriliyor: {indirme_url}")
        response = requests.get(indirme_url, stream=True, timeout=60)
        
        if response.status_code != 200:
            logger.error(f"İndirme hatası: {response.status_code}")
            return False
        
        # Dosyayı kaydet
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # ZIP dosyasını çıkart
        logger.info("Güncelleme paketi açılıyor...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # Güncelleme betiğini çalıştır
        guncelleme_betik = os.path.join(temp_dir, "guncelle.bat")
        if os.path.exists(guncelleme_betik):
            logger.info("Güncelleme betiği çalıştırılıyor...")
            
            # Mevcut uygulamayı kapat ve güncelleme betiğini çalıştır
            # Not: Bu işlem uygulamayı kapatacak ve yeniden başlatacak
            subprocess.Popen([guncelleme_betik, os.getcwd()], 
                            creationflags=subprocess.CREATE_NEW_CONSOLE)
            
            # Uygulamayı kapat
            logger.info("Uygulama güncelleme için kapatılıyor...")
            time.sleep(2)  # Güncelleme betiğinin başlaması için bekle
            sys.exit(0)
        else:
            logger.error("Güncelleme betiği bulunamadı")
            return False
            
    except Exception as e:
        logger.error(f"Güncelleme indirme hatası: {str(e)}")
        return False

def otomatik_guncelleme_kontrol():
    """Otomatik güncelleme kontrolü yapar"""
    guncelleme_bilgisi = surum_kontrol()
    if guncelleme_bilgisi:
        return guncelleme_indir(guncelleme_bilgisi)
    return False

# Test için
if __name__ == "__main__":
    otomatik_guncelleme_kontrol()