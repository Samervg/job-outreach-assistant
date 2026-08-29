# Job Outreach Assistant

İş başvurusu sürecimi daha düzenli yönetmek ve aynı zamanda FastAPI, Streamlit, SQLite, Gmail API ve yerel LLM kullanımı konusunda kendimi geliştirmek için hazırladığım kişisel bir proje.

Uygulama üzerinden şirketleri ve başvuruları takip edebiliyor, şirket web sitesinden temel bilgiler çıkarabiliyor, Ollama ile kişiselleştirilmiş başvuru e-postaları oluşturabiliyor ve Gmail üzerinden gönderim yapabiliyorum.

Gönderilen başvuruların yanıtları da Gmail üzerinden takip edilebiliyor. Gelen yanıtlar yerel model ile analiz edilebiliyor ve gerektiğinde kullanıcı onayıyla follow-up e-postası hazırlanabiliyor.

Proje şu anda yerel ve tek kullanıcıya yönelik çalışıyor.

## Özellikler

- Profil ve PDF CV yönetimi
- PDF CV analizi
- Şirket ve pozisyon yönetimi
- Şirket web sitesinden bilgi ve açık pozisyon çıkarma
- Ollama ile kişiselleştirilmiş başvuru e-postası oluşturma
- Taslakları düzenleme ve kaydetme
- Gmail OAuth 2.0 bağlantısı
- Kullanıcı onayıyla Gmail üzerinden e-posta gönderme
- Gönderilen başvuruların Gmail konuşmalarını takip etme
- Gelen yanıt içeriğini görüntüleme
- Ollama ile gelen yanıtı analiz etme
- Yanıt durumuna göre kullanıcıya durum değişikliği önerme
- Güvenli follow-up taslağı oluşturma ve gönderme
- Başvuru durum geçmişini takip etme
- Başvuru istatistikleri ve dönüşüm oranları
- Duruma göre başvuru filtreleme
- Başvurulara özel not ekleme
- Draft ve failed durumundaki başvuruları güvenli şekilde silme
- SQLite üzerinde transaction ve durum geçmişi yönetimi

AI tarafından yapılan durum değerlendirmeleri otomatik olarak uygulanmaz. Son karar kullanıcıya bırakılır.

## Kullanılan Teknolojiler

- **Python**
- **FastAPI** — backend ve REST API
- **Streamlit** — kullanıcı arayüzü
- **SQLite** — yerel veritabanı
- **Ollama / Qwen3:4b** — yerel LLM
- **Gmail API** — e-posta gönderme ve yanıt takibi
- **OAuth 2.0** — Gmail yetkilendirmesi
- **Pytest** — testler

Uygulamanın genel yapısı:

```text
Streamlit
    ↓
FastAPI REST API
    ↓
Service katmanı
    ↓
SQLite / Gmail API / Ollama / şirket web siteleri
```

Streamlit veritabanına doğrudan erişmez. Frontend işlemleri FastAPI üzerinden gerçekleştirir.

## Ollama Kurulumu

Ollama'yı Windows Package Manager ile kurabilirsiniz:

```powershell
winget install --id Ollama.Ollama -e
```

Alternatif olarak Ollama'nın resmi sitesinden Windows kurulum dosyası kullanılabilir.

Varsayılan olarak projede `qwen3:4b` kullanıyorum:

```powershell
ollama pull qwen3:4b
```

Ollama otomatik başlamadıysa:

```powershell
ollama serve
```

Kurulu modelleri kontrol etmek için:

```powershell
ollama list
```

Model `.env` dosyası üzerinden değiştirilebilir. Uygulama modeli otomatik olarak indirmez.

## Gmail OAuth Kurulumu

1. Google Cloud Console üzerinden yeni bir proje oluşturun veya mevcut bir projeyi seçin.
2. Gmail API'yi etkinleştirin.
3. Google Auth Platform üzerinden OAuth izin ekranını yapılandırın.
4. Uygulama test modundaysa kullanılacak Gmail hesabını test kullanıcısı olarak ekleyin.
5. Data Access bölümünde şu izinleri ekleyin:

```text
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.readonly
openid
email
```

6. OAuth Client oluştururken **Web application** seçin.

Redirect URI:

```text
http://127.0.0.1:8000/gmail/auth/callback
```

7. İndirilen client secret dosyasını şu konuma koyun:

```text
credentials/client_secret.json
```

Gmail tokenları, client secret ve bağlantılı hesap bilgileri yalnızca yerel `credentials/` klasöründe tutulur. Bu klasör Git tarafından takip edilmez.

`gmail.readonly` izni genel bir gelen kutusu okuyucusu oluşturmak için kullanılmıyor. Uygulama yalnızca mevcut bir başvuruyla ilişkili Gmail konuşmasını kullanıcı isteğiyle kontrol ediyor.

Yerel OAuth callback'i HTTP kullandığı için geliştirme ortamında:

```dotenv
ALLOW_INSECURE_OAUTH_LOOPBACK=true
```

kullanılıyor.

Bu ayar yalnızca localhost/loopback geliştirme ortamı içindir. Public bir deployment durumunda HTTPS kullanılmalıdır.

## Projenin Kurulumu

Projeyi klonlayın:

```powershell
Set-Location "$env:USERPROFILE\Desktop"

git clone https://github.com/Samervg/job-outreach-assistant.git

cd job-outreach-assistant
```

Sanal ortam oluşturun:

```powershell
python -m venv .venv

.\.venv\Scripts\Activate.ps1
```

Bağımlılıkları yükleyin:

```powershell
python -m pip install --upgrade pip

pip install -r requirements.txt
```

Örnek environment dosyasını kopyalayın:

```powershell
Copy-Item .env.example .env
```

Temel `.env` ayarları:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b

GMAIL_CLIENT_SECRET_PATH=credentials/client_secret.json
GMAIL_REDIRECT_URI=http://127.0.0.1:8000/gmail/auth/callback

ALLOW_INSECURE_OAUTH_LOOPBACK=true

FRONTEND_URL=http://localhost:8501

LOG_LEVEL=INFO
BACKUP_DIR=data/backups
```

`.venv`, `.env`, SQLite veritabanı, Gmail credentials/token dosyaları ve yüklenen CV dosyaları GitHub'a gönderilmez.

## Uygulamayı Çalıştırma

İlk terminalde FastAPI backend:

```powershell
Set-Location "$env:USERPROFILE\Desktop\job-outreach-assistant"

.\.venv\Scripts\Activate.ps1

python -m uvicorn backend.main:app --reload
```

İkinci terminalde Streamlit:

```powershell
Set-Location "$env:USERPROFILE\Desktop\job-outreach-assistant"

.\.venv\Scripts\Activate.ps1

python -m streamlit run frontend\app.py
```

Uygulama:

```text
http://localhost:8501
```

FastAPI Swagger dokümantasyonu:

```text
http://127.0.0.1:8000/docs
```

Backend sağlık kontrolü:

```text
http://127.0.0.1:8000/health
```

## Veritabanı Yedeği

Uygulama otomatik yedek veya geri yükleme yapmaz. İhtiyaç halinde çalışan
SQLite veritabanının tutarlı ve zaman damgalı bir kopyasını manuel olarak
oluşturabilirsiniz:

```powershell
python -m backend.backup
```

Yedekler varsayılan olarak `data/backups/` klasörüne yazılır. Konum
`BACKUP_DIR` ile değiştirilebilir. Bu klasör Git tarafından takip edilmez;
geri yükleme işlemi bilinçli olarak otomatikleştirilmemiştir.

Backend log seviyesi `LOG_LEVEL` ile ayarlanabilir. Varsayılan `INFO` seviyesi
başlangıç, veritabanı ve dış servis hata türlerini kaydeder; OAuth tokenları,
client secret, CV/e-posta içerikleri ve Gmail yanıt gövdeleri loglanmaz.

## Başvuru Akışı

Yeni bir başvuru oluştururken temel akış şu şekilde:

```text
Şirket / Pozisyon
        ↓
Şirket araştırması
        ↓
CV ve profil bilgileri
        ↓
Ollama ile e-posta taslağı
        ↓
Kullanıcı düzenlemesi
        ↓
Açık gönderim onayı
        ↓
Gmail
        ↓
Yanıt takibi
        ↓
Yanıt analizi
        ↓
Follow-up / durum güncellemesi
```

E-posta gönderimi tamamen kullanıcı kontrollüdür. AI kendi başına e-posta göndermez veya başvuru durumunu değiştirmez.

## Yanıt Takibi

Gönderilen bir başvurunun Gmail message/thread bilgileri veritabanında tutulur.

Kullanıcı yanıt kontrolü yaptığında uygulama yalnızca ilgili Gmail konuşmasını kontrol eder.

Yeni bir yanıt bulunduğunda:

- Gönderen
- Konu
- Yanıt zamanı
- Yanıt sayısı

gibi bilgiler kaydedilir.

Yanıtın tam içeriği SQLite veritabanında tutulmaz. Kullanıcı istediğinde Gmail üzerinden okunur.

Yanıt Ollama ile analiz edilerek örneğin:

- olumlu dönüş
- mülakat
- ret
- ek bilgi talebi
- otomatik yanıt
- nötr/belirsiz

şeklinde sınıflandırılabilir.

Bu analiz yalnızca öneridir. Başvuru durumu kullanıcı onayı olmadan değiştirilmez.

## Follow-up

Yanıt gelmeyen başvurular için belirlenen süre sonunda follow-up oluşturulabilir.

Follow-up gönderilmeden önce uygulama tekrar Gmail konuşmasını kontrol eder. Bu sırada yeni bir yanıt geldiyse gönderim iptal edilir.

Follow-up:

- aynı Gmail thread'i üzerinden gönderilir
- kullanıcı tarafından düzenlenebilir
- açık kullanıcı onayı gerektirir
- maksimum takip sayısıyla sınırlandırılabilir
- başvuru bazında kapatılabilir

## Durum Geçmişi

Başvuruların durum değişiklikleri ayrı bir geçmiş tablosunda tutulur.

Örnek:

```text
Taslak
  ↓
Gönderildi
  ↓
Yanıt Geldi
  ↓
Mülakat
```

Geçmiş kayıtları sonradan değiştirilmez veya silinmez.

Kullanıcı bir durumu düzelttiğinde eski kayıt silinmek yerine yeni bir düzeltme kaydı oluşturulur.

Bu geçmiş aynı zamanda başvuru analizlerinde kullanılır.

## Başvuru Analizi

Dashboard üzerinde temel başvuru istatistikleri gösterilir:

- Toplam başvuru
- Gönderilen
- Yanıtlanan
- Mülakat
- Ret
- Teklif
- Yanıt oranı
- Başvurudan mülakata dönüşüm
- Yanıttan mülakata dönüşüm
- Mülakattan teklife dönüşüm
- Ortalama yanıt süresi
- Ortalama mülakata ulaşma süresi
- Yanıt bekleyen başvurular
- Follow-up gereken başvurular

Eski kayıtların tamamında gerçek durum geçiş zamanları bulunmadığı için bazı süre analizleri yalnızca yeterli geçmiş verisi olan başvurularda hesaplanır.

## Veri Güvenliği

Kişisel veriler yerel olarak tutulur.

Git tarafından takip edilmeyen başlıca dosyalar:

```text
.env
credentials/
data/job_outreach.db
data/uploads/
.venv/
```

Gmail gönderiminden önce backend:

- taslağı
- alıcıyı
- konuyu
- e-posta metnini
- aktif CV'yi
- Gmail bağlantısını
- kullanıcı onayını

tekrar doğrular.

Gönderilmiş bir taslak tekrar gönderilemez.

Follow-up gönderiminde de gönderimden hemen önce yeni Gmail yanıtı olup olmadığı tekrar kontrol edilir.

## Testler

Projede backend ve kritik kullanıcı akışları için otomatik testler bulunuyor.

Şu an test paketi:

```text
127 test
```

Testlerde gerçek Gmail gönderimi veya gerçek Gmail okuması yapılmaz. Gmail ve diğer dış servisler mock edilerek test edilir.

Ayrıca geliştirme sırasında SQLite için:

```sql
PRAGMA integrity_check;
```

kontrolü kullanılıyor.

## Projenin Durumu

Proje şu anda aktif olarak geliştirdiğim kişisel bir çalışma.

Şu ana kadar ağırlıklı olarak:

- REST API yapısı
- frontend/backend ayrımı
- SQLite ve transaction yönetimi
- OAuth 2.0
- Gmail API
- yerel LLM entegrasyonu
- durum geçmişi
- güvenli follow-up akışı
- test/mocking
- temel uygulama analitiği

konularında pratik yapmak için geliştirdim.

İlerleyen aşamalarda hata yönetimi, logging, configuration yönetimi ve genel production güvenilirliği üzerinde çalışmayı planlıyorum.
