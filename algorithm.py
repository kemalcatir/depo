"""
Nöbet Planlama Algoritması
Tüm 9 kurala uygun heuristic greedy search algoritması
Ay geçişlerinde istirahat kuralı uygulanır
"""

import sqlite3
import calendar
import logging
from datetime import datetime, timedelta

# Logging konfigürasyonu
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_NAME = "nobet_sistemi.db"

def generate_plan(year, month):
    """
    Nöbet planlama ana fonksiyonu
    
    Parametreler:
        year (int): Yıl (ör: 2026)
        month (int): Ay (ör: 6)
    
    Döndürür:
        dict: {
            'status': 'success' veya 'error',
            'data': {'YYYY-MM-DD': [isim1, isim2, ...]},
            'stats': {'İsim': {'total': 8, 'thursday': 2, ...}},
            'warnings': {'empty_days': 0, 'partial_days': 0}
        }
    """
    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"📅 Plan oluşturuluyor: {year}-{month:02d}")
        logger.info(f"{'='*60}\n")
        
        # Veritabanı bağlantısı
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # 1. SİSTEM AYARLARINI YÜKLESİ
        cursor.execute("""
            SELECT nobetci_sayisi, puan_total, puan_ayni_gun, puan_dinlenme,
                   puan_pazartesi, puan_sali, puan_carsamba, puan_persembe, 
                   puan_cuma, puan_cumartesi, puan_pazar
            FROM ayarlar WHERE id=1
        """)
        settings_row = cursor.fetchone()
        
        if not settings_row:
            logger.error("❌ Sistem ayarları bulunamadı!")
            conn.close()
            return {"status": "error", "message": "Sistem ayarları bulunamadı!"}
        
        settings = {
            'nobetci_sayisi': settings_row[0] or 6,
            'puan_total': settings_row[1] or 50,
            'puan_pazartesi': settings_row[4] or 0,
            'puan_sali': settings_row[5] or 0,
            'puan_carsamba': settings_row[6] or 0,
            'puan_persembe': settings_row[7] or 0,
            'puan_cuma': settings_row[8] or 0,
            'puan_cumartesi': settings_row[9] or 80,
            'puan_pazar': settings_row[10] or 80,
            'min_rest_days': 2
        }
        logger.info(f"✅ Sistem ayarları: {settings['nobetci_sayisi']} kişi/gün")
        
        # 2. ÖZEL GÜNLERİ YÜKLESİ
        cursor.execute("SELECT tarih, puan FROM ozel_gun_puanlari ORDER BY tarih")
        special_days = {row[0]: row[1] for row in cursor.fetchall()}
        logger.info(f"✅ Özel günler: {len(special_days)} gün")
        
        # 3. AKTİF PERSONELİ YÜKLESİ
        cursor.execute("SELECT id, unvan, ad_soyad FROM personel WHERE aktif_mi=1 ORDER BY ad_soyad")
        personnel = cursor.fetchall()
        
        if not personnel:
            logger.error("❌ Aktif personel bulunamadı!")
            conn.close()
            return {"status": "error", "message": "Aktif personel bulunamadı!"}
        
        personnel_ids = [p[0] for p in personnel]
        personnel_dict = {p[0]: f"{p[1]} {p[2]}" if p[1] else p[2] for p in personnel}
        logger.info(f"👥 Aktif personel: {len(personnel_ids)} kişi")
        
        # 4. KISITLAMALARI YÜKLESİ (İzin, mazeret)
        cursor.execute("""
            SELECT personel_id, tarih 
            FROM kisitlamalar 
            WHERE strftime('%Y', tarih) = ? AND strftime('%m', tarih) = ?
        """, (str(year), f"{month:02d}"))
        restrictions = {}
        for p_id, tarih in cursor.fetchall():
            if p_id not in restrictions:
                restrictions[p_id] = set()
            restrictions[p_id].add(tarih)
        logger.info(f"🚫 Kısıtlamalar: {len(restrictions)} personel")
        
        # 5. ÖNCEKİ AY VERİLERİNİ YÜKLESİ (Kural 3 & 4)
        # GÜNCELLENMIŞ: Ay geçişlerinde de istirahat kuralı uygulanır
        target = datetime(year, month, 1) - timedelta(days=1)
        prev_y, prev_m = target.year, target.month
        
        # Önceki ayın verilerini yükle
        cursor.execute("""
            SELECT personel_id, tarih 
            FROM aylik_nobetler 
            WHERE strftime('%Y', tarih) = ? AND strftime('%m', tarih) = ?
        """, (str(prev_y), f"{prev_m:02d}"))
        
        prev_month_data = {}
        last_duty_dates = {}
        
        for p_id, tarih in cursor.fetchall():
            if p_id not in prev_month_data:
                prev_month_data[p_id] = {'days': set(), 'weekend_score': 0}
            
            try:
                tarih_dt = datetime.strptime(tarih, "%Y-%m-%d")
                prev_month_data[p_id]['days'].add(tarih_dt.day)
                
                # Haftasonu puanı (Kural 3)
                if tarih_dt.weekday() >= 5:
                    prev_month_data[p_id]['weekend_score'] += 1
                
                # Son nöbet tarihi (Kural 4)
                if p_id not in last_duty_dates or tarih_dt > last_duty_dates[p_id]:
                    last_duty_dates[p_id] = tarih_dt
            except ValueError:
                continue
        
        logger.info(f"📊 Önceki ay: {len(prev_month_data)} personel")
        
        # YENI: Önceki ayın son nöbetçilerinin en son nöbet tarihini al
        # (Ay geçişinde istirahat kuralını uygulamak için)
        cursor.execute("""
            SELECT personel_id, MAX(tarih) as last_tarih
            FROM aylik_nobetler 
            WHERE personel_id IN ({})
            GROUP BY personel_id
        """.format(','.join('?' * len(personnel_ids))), personnel_ids)
        
        for p_id, last_tarih in cursor.fetchall():
            try:
                tarih_dt = datetime.strptime(last_tarih, "%Y-%m-%d")
                if p_id not in last_duty_dates or tarih_dt > last_duty_dates[p_id]:
                    last_duty_dates[p_id] = tarih_dt
            except ValueError:
                continue
        
        logger.info(f"📋 Tüm kişilerin son nöbeti yüklendi: {len(last_duty_dates)} personel")
        conn.close()
        
        # 6. İSTATİSTİKLERİ BAŞLAT
        stats = {}
        saturday_consecutive = {}
        
        for p_id in personnel_ids:
            stats[p_id] = {
                'total': 0,
                'thursday': 0,
                'friday': 0,
                'saturday': 0,
                'sunday': 0,
                'last_duty': last_duty_dates.get(p_id)
            }
            saturday_consecutive[p_id] = False
        
        # 7. PLAN OLUŞTUR
        roster = {}
        num_days = calendar.monthrange(year, month)[1]
        num_per_day = settings['nobetci_sayisi']
        empty_days = 0
        partial_days = 0
        
        logger.info(f"🔄 {num_days} gün için plan hazırlanıyor...\n")
        
        for day in range(1, num_days + 1):
            current_date = datetime(year, month, day)
            date_str = current_date.strftime("%Y-%m-%d")
            weekday = current_date.weekday()  # 0=Monday, 6=Sunday
            current_day = current_date.day
            
            candidates = []
            
            # Her personel için uygunluk kontrol et
            for p_id in personnel_ids:
                # HARD CONSTRAINTS
                
                # Kısıtlama kontrolü
                if p_id in restrictions and date_str in restrictions[p_id]:
                    continue
                
                # Kural 5: İstirahat kuralı (Ay geçişlerinde de uygulanır)
                last_duty = stats[p_id]['last_duty']
                if last_duty and isinstance(last_duty, datetime):
                    days_rest = (current_date - last_duty).days
                    if days_rest < settings['min_rest_days']:
                        continue
                
                # Kural 9: Cumartesi üst üste engel
                if weekday == 5:  # Cumartesi
                    if saturday_consecutive.get(p_id, False):
                        continue
                
                # SOFT CONSTRAINTS - PUANLAMA
                priority = 1000000000
                
                # Kural 1: Adil dağılım
                total = stats[p_id]['total']
                priority -= (total * settings['puan_total'])
                
                # Kural 2: Perşembe-Cuma eşit
                if weekday == 3:
                    priority -= (stats[p_id]['thursday'] * 8000)
                elif weekday == 4:
                    priority -= (stats[p_id]['friday'] * 8000)
                
                # Kural 3: Haftasonu (önceki ay puanına göre)
                if weekday >= 5:
                    prev_score = prev_month_data.get(p_id, {}).get('weekend_score', 0)
                    priority -= (prev_score * 50000)
                    
                    if weekday == 5:
                        priority -= (stats[p_id]['saturday'] * 12000)
                    else:
                        priority -= (stats[p_id]['sunday'] * 12000)
                
                # Kural 6 & 8: Gün puanlaması
                date_str_check = current_date.strftime("%Y-%m-%d")
                if date_str_check in special_days:
                    day_score = special_days[date_str_check]
                else:
                    day_scores = {0: settings['puan_pazartesi'], 1: settings['puan_sali'],
                                 2: settings['puan_carsamba'], 3: settings['puan_persembe'],
                                 4: settings['puan_cuma'], 5: settings['puan_cumartesi'],
                                 6: settings['puan_pazar']}
                    day_score = day_scores.get(weekday, 0)
                
                priority -= (total * day_score * 100)
                
                # Kural 8: Önceki ay aynı güne çakışmasını engelle
                if current_day in prev_month_data.get(p_id, {}).get('days', set()):
                    priority -= 50000
                
                candidates.append((priority, p_id))
            
            # Günlük seçim
            if not candidates:
                logger.warning(f"🚫 {date_str}: Uygun personel yok")
                empty_days += 1
                roster[date_str] = []
                continue
            
            candidates.sort(key=lambda x: x[0], reverse=True)
            selected = candidates[:num_per_day]
            selected_ids = [c[1] for c in selected]
            
            if len(selected_ids) < num_per_day:
                partial_days += 1
            
            roster[date_str] = [personnel_dict.get(pid, "Bilinmiyor") for pid in selected_ids]
            
            # İstatistikleri güncelle
            for p_id in selected_ids:
                stats[p_id]['last_duty'] = current_date
                stats[p_id]['total'] += 1
                
                if weekday == 3:
                    stats[p_id]['thursday'] += 1
                elif weekday == 4:
                    stats[p_id]['friday'] += 1
                elif weekday == 5:
                    stats[p_id]['saturday'] += 1
                    saturday_consecutive[p_id] = True
                elif weekday == 6:
                    stats[p_id]['sunday'] += 1
            
            # Cumartesi flag sıfırlama
            if weekday != 5:
                for p_id in personnel_ids:
                    if p_id not in selected_ids:
                        saturday_consecutive[p_id] = False
        
        # Sonuçları formatla
        final_stats = {}
        for p_id in personnel_ids:
            name = personnel_dict[p_id]
            final_stats[name] = {
                'total': stats[p_id]['total'],
                'thursday': stats[p_id]['thursday'],
                'friday': stats[p_id]['friday'],
                'saturday': stats[p_id]['saturday'],
                'sunday': stats[p_id]['sunday'],
                'last_duty': stats[p_id]['last_duty'].strftime("%Y-%m-%d") if stats[p_id]['last_duty'] else None
            }
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ Plan tamamlandı!")
        logger.info(f"   📊 Boş gün: {empty_days}")
        logger.info(f"   ⚠️  Kısmi atama: {partial_days}")
        logger.info(f"{'='*60}\n")
        
        return {
            "status": "success",
            "data": roster,
            "stats": final_stats,
            "warnings": {
                "empty_days": empty_days,
                "partial_days": partial_days
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Plan oluşturma hatası: {str(e)}")
        return {"status": "error", "message": f"Hata: {str(e)}"}
