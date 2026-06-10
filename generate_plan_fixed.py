import sqlite3
import calendar
import logging
from datetime import datetime, timedelta

# Logging konfigürasyonu
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_NAME = "nobet_sistemi.db"
MIN_REST_DAYS = 2  # Hard Constraint: Maksimum İstirahat Süresi

def parse_date(tarih):
    """
    Çeşitli tarih formatlarını destekler.
    Hata: Orijinal kodda tarih formatı kontrol edilmiyordu.
    """
    if isinstance(tarih, datetime):
        return tarih
    
    if isinstance(tarih, str):
        formats = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(tarih, fmt)
            except ValueError:
                continue
        raise ValueError(f"Geçersiz tarih formatı: {tarih}")
    
    raise ValueError(f"Bilinmeyen tarih tipi: {type(tarih)}")

def get_settings_from_db():
    """Sistem ayarlarını (günlük nöbetçi sayısı vb.) getirir."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nobetci_sayisi FROM ayarlar WHERE id=1")
        row = cursor.fetchone()
        nobetci_sayisi = row[0] if row else 6
        logger.info(f"Ayarlar yüklendi: Günlük nöbetçi = {nobetci_sayisi}")
    except sqlite3.OperationalError as e:
        logger.warning(f"Ayarlar tablosu okunamadı: {e}. Default değer kullanılıyor.")
        nobetci_sayisi = 6
    finally:
        conn.close()
    
    return {"nobetci_sayisi": nobetci_sayisi}

def get_restrictions_for_month(year, month):
    """Personelin o ay içindeki mazeret/kısıtlı günlerini getirir."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT personel_id, tarih 
            FROM kisitlamalar 
            WHERE strftime('%Y', tarih) = ? AND strftime('%m', tarih) = ?
        """, (str(year), f"{month:02d}"))
        rows = cursor.fetchall()
    except sqlite3.OperationalError as e:
        logger.warning(f"Kısıtlamalar okunurken hata: {e}")
        rows = []
    finally:
        conn.close()

    restrictions = {}
    for p_id, tarih in rows:
        if p_id not in restrictions:
            restrictions[p_id] = set()
        restrictions[p_id].add(tarih)
    
    logger.info(f"{len(restrictions)} personelin kısıtlaması bulundu")
    return restrictions

def get_prev_month_data(year, month):
    """
    Kural 6: Ay sonuna devreden istirahat kuralı için son nöbeti getirir.
    Kural 8: Geçen ay aynı güne çakışmayı önlemek için tutulan günleri getirir.
    Haftasonu Önceliği: Geçen ayın toplam nöbet sayısını 'kümülatif puan' olarak hesaplar.
    """
    target = datetime(year, month, 1) - timedelta(days=1)
    prev_y, prev_m = target.year, target.month

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT personel_id, tarih 
            FROM aylik_nobetler 
            WHERE strftime('%Y', tarih) = ? AND strftime('%m', tarih) = ?
        """, (str(prev_y), f"{prev_m:02d}"))
        rows = cursor.fetchall()
    except sqlite3.OperationalError as e:
        logger.warning(f"Önceki ay verileri okunurken hata: {e}")
        rows = []
    finally:
        conn.close()

    prev_data = {}
    last_duty_dates = {}
    
    for p_id, tarih in rows:
        if p_id not in prev_data:
            prev_data[p_id] = {'days': set(), 'total_count': 0}
        
        try:
            tarih_dt = parse_date(tarih)  # FİKS: Tarih parsing geliştirildi
        except ValueError as e:
            logger.warning(f"Tarih parse hatası: {e}. Atlaniyor.")
            continue
        
        # Günleri ve toplam sayıyı (puanı) kaydet
        prev_data[p_id]['days'].add(tarih_dt.day)
        prev_data[p_id]['total_count'] += 1
        
        # En son nöbeti kaydet (Devreden istirahat kuralı için)
        if p_id not in last_duty_dates or tarih_dt > last_duty_dates[p_id]:
            last_duty_dates[p_id] = tarih_dt
    
    logger.info(f"Önceki ay: {len(prev_data)} personel verisi yüklendi")
    return prev_data, last_duty_dates

def generate_plan(year, month):
    """
    Nöbet Planlama Algoritması (Heuristic Greedy Search)
    
    FİKSLER:
    1. Priority başlangıç değeri 100000000 olarak artırıldı
    2. Boş candidate durumu kontrol edildi
    3. Database hataları logging ile izleniyor
    4. Tarih parsing geliştirildi
    """
    logger.info(f"Plan oluşturuluyor: {year}-{month:02d}")
    
    settings = get_settings_from_db()
    num_per_day = settings.get('nobetci_sayisi', 6)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, ad_soyad FROM personel WHERE aktif_mi=1")
    personnel = cursor.fetchall()
    conn.close()

    personnel_ids = [p[0] for p in personnel]
    personnel_dict = {p[0]: p[1] for p in personnel}

    if not personnel_ids:
        logger.error("Aktif personel bulunamadı!")
        return {"status": "error", "message": "Aktif personel bulunamadı!"}

    logger.info(f"Toplam aktif personel: {len(personnel_ids)}")

    prev_month_data, last_duty_dates = get_prev_month_data(year, month)
    restrictions = get_restrictions_for_month(year, month)

    # State (Durum) Matrisi Başlatılıyor
    stats = {p_id: {
        'total': 0, 'thursday': 0, 'friday': 0, 'weekend': 0, 
        'last_duty': last_duty_dates.get(p_id) 
    } for p_id in personnel_ids}

    roster = {}
    num_days = calendar.monthrange(year, month)[1]
    
    # İstatistik takibi
    empty_days_count = 0
    partial_days_count = 0

    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        date_str = current_date.strftime("%Y-%m-%d")
        weekday = current_date.weekday()
        current_day = current_date.day

        candidates = []

        for p_id in personnel_ids:
            # === 1. ZORUNLU KISITLAR (HARD CONSTRAINTS) ===
            
            # Kısıtlamalar/İzin Kontrolü
            if p_id in restrictions and date_str in restrictions[p_id]:
                continue 

            # Maksimum İstirahat ve Devreden Ay Kuralı Kontrolü
            last_duty = stats[p_id]['last_duty']
            if last_duty and isinstance(last_duty, datetime):  # FİKS: Type check eklendi
                if (current_date - last_duty).days < MIN_REST_DAYS:
                    continue

            # === 2. DAĞILIM KISITLARI VE SEZGİSEL PUANLAMA (SOFT CONSTRAINTS) ===
            priority = 100000000  # FİKS: 10000 yerine 100000000 (underflow önlemek için)

            # Kural 8: Önceki ayla aynı güne çakışma (Ağır Ceza)
            if current_day in prev_month_data.get(p_id, {}).get('days', set()):
                priority -= 999999

            # Adil Dağılım: Çok nöbet tutanın seçilme ihtimalini düşür
            total_assigned = stats[p_id]['total']
            priority -= (total_assigned * 2000)

            # Özel Gün Dağılımı (Perşembe ve Cuma)
            if weekday == 3: 
                priority -= (stats[p_id]['thursday'] * 3000)
            elif weekday == 4: 
                priority -= (stats[p_id]['friday'] * 3000)
            
            # Haftasonu Önceliği ve Puanlama Kuralı
            if weekday >= 5:
                # O ayki haftasonu eşitliği için ceza
                priority -= (stats[p_id]['weekend'] * 4000)
                
                # Önceki ayın kümülatif puanı (nöbet sayısı) yüksekse haftasonu ihtimalini sert düşür
                prev_month_score = prev_month_data.get(p_id, {}).get('total_count', 0)
                priority -= (prev_month_score * 1500)

            candidates.append((priority, p_id))

        # === 3. GÜNLÜK SEÇİM VE GÜNCELLEME ===
        
        # FİKS: Boş candidate durumu kontrol ediliyor
        if not candidates:
            logger.warning(f"{date_str}: Hiçbir uygun personel bulunamadı (tüm kısıtlandı)")
            empty_days_count += 1
            roster[date_str] = []
            continue
        
        # En yüksek puanlıları (en uygunları) sırala
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Günlük sayıyı al
        selected = candidates[:num_per_day]
        selected_ids = [c[1] for c in selected]
        
        # FİKS: Kısmi atama kontrolü
        if len(selected_ids) < num_per_day:
            logger.warning(f"{date_str}: Sadece {len(selected_ids)}/{num_per_day} personel atandı")
            partial_days_count += 1

        # Roster'a isimleri kaydet
        roster[date_str] = [personnel_dict.get(pid, "Bilinmiyor") for pid in selected_ids]

        # State (Durum) Matrisini bir sonraki gün için güncelle
        for p_id in selected_ids:
            stats[p_id]['last_duty'] = current_date
            stats[p_id]['total'] += 1
            if weekday == 3: stats[p_id]['thursday'] += 1
            elif weekday == 4: stats[p_id]['friday'] += 1
            elif weekday >= 5: stats[p_id]['weekend'] += 1

    # Frontend/GUI için istatistikleri isim bazlı formatla
    final_stats = {personnel_dict[pid]: stats[pid] for pid in personnel_ids}

    logger.info(f"Plan oluşturuldu: {empty_days_count} boş gün, {partial_days_count} kısmi atama")
    
    return {
        "status": "success",
        "data": roster,
        "stats": final_stats,
        "warnings": {
            "empty_days": empty_days_count,
            "partial_days": partial_days_count
        }
    }
