import os
import requests
from dotenv import load_dotenv

# .env dosyasından çevresel değişkenleri yükle
load_dotenv()

# Twilio WhatsApp API bilgileri
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')  # +14155238886 gibi

def whatsapp_mesaj_gonder(telefon, mesaj):
    """
    Twilio WhatsApp API kullanarak mesaj gönderir
    
    Args:
        telefon (str): Alıcının telefon numarası (90xxxxxxxxxx formatında)
        mesaj (str): Gönderilecek mesaj
    
    Returns:
        bool: Mesaj başarıyla gönderildiyse True, aksi halde False
    """
    try:
        # Telefon numarasını WhatsApp formatına çevir
        if not telefon.startswith('+'):
            telefon = '+' + telefon
            
        # Twilio API endpoint
        url = f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json'
        
        # API isteği için veri
        data = {
            'To': f'whatsapp:{telefon}',
            'From': f'whatsapp:{TWILIO_WHATSAPP_NUMBER}',
            'Body': mesaj
        }
        
        # API isteği gönder
        response = requests.post(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data=data
        )
        
        # Yanıtı kontrol et
        if response.status_code == 201:
            print(f"WhatsApp mesajı başarıyla gönderildi: {telefon}")
            return True
        else:
            print(f"WhatsApp mesajı gönderilemedi: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"WhatsApp mesajı gönderirken hata oluştu: {str(e)}")
        return False

# Alternatif olarak, WhatsApp Cloud API kullanımı (Meta/Facebook)
def whatsapp_cloud_mesaj_gonder(telefon, mesaj):
    """
    Meta WhatsApp Cloud API kullanarak mesaj gönderir
    
    Args:
        telefon (str): Alıcının telefon numarası (90xxxxxxxxxx formatında)
        mesaj (str): Gönderilecek mesaj
    
    Returns:
        bool: Mesaj başarıyla gönderildiyse True, aksi halde False
    """
    try:
        # WhatsApp Cloud API bilgileri
        access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
        phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
        
        # Telefon numarasını WhatsApp formatına çevir
        if telefon.startswith('+'):
            telefon = telefon[1:]
            
        # API endpoint
        url = f'https://graph.facebook.com/v17.0/{phone_number_id}/messages'
        
        # API isteği için veri
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'messaging_product': 'whatsapp',
            'to': telefon,
            'type': 'text',
            'text': {
                'body': mesaj
            }
        }
        
        # API isteği gönder
        response = requests.post(url, headers=headers, json=data)
        
        # Yanıtı kontrol et
        if response.status_code == 200:
            print(f"WhatsApp Cloud API mesajı başarıyla gönderildi: {telefon}")
            return True
        else:
            print(f"WhatsApp Cloud API mesajı gönderilemedi: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"WhatsApp Cloud API mesajı gönderirken hata oluştu: {str(e)}")
        return False