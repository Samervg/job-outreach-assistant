# Job Outreach Assistant — Phase 6

Yerelde çalışan, tek kullanıcıya yönelik iş başvurusu iletişim asistanı MVP'si.

Mevcut özellikler:

- Profil ve PDF CV yönetimi
- Manuel şirket yönetimi
- Yerel Ollama modeliyle kişiselleştirilmiş e-posta taslağı üretimi
- PDF CV analizi ve hedef pozisyona uygun kanıt seçimi
- Taslakları görüntüleme, düzenleme ve SQLite'a kaydetme
- Tek bir Gmail hesabını OAuth 2.0 ile bağlama
- Açık kullanıcı onayından sonra tek taslağı aktif PDF CV ile gönderme
- Güvenli SQLite bağlantı ve transaction yönetimi

Toplu gönderim, otomatik takip, zamanlama, scraping, şirket keşfi ve gelen kutusu
okuma bulunmaz.

## Gereksinimler

- Python 3.10 veya daha yeni bir sürüm
- Windows PowerShell
- Yerel [Ollama](https://ollama.com/download/windows) kurulumu

## Ollama kurulumu

Ollama'yı Windows Package Manager ile kurun:

```powershell
winget install --id Ollama.Ollama -e
```

Alternatif olarak resmi Windows kurulum dosyasını `https://ollama.com/download/windows` adresinden indirebilirsiniz.

Varsayılan geliştirme modelini indirin:

```powershell
ollama pull qwen3:4b
```

Ollama otomatik başlamadıysa ayrı bir terminalde çalıştırın:

```powershell
ollama serve
```

Kurulu modelleri ve Ollama API'sini kontrol edin:

```powershell
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

`qwen3:4b`, daha büyük modellere göre yerel geliştirme için daha ulaşılabilir bir 4B modelidir ve talimat takip yeteneği bu MVP'nin kısa, yapılandırılmış e-posta üretimi için uygundur. Model `.env` üzerinden değiştirilebilir; uygulama modeli otomatik indirmez.

## Gmail OAuth kurulumu

1. [Google Cloud Console](https://console.cloud.google.com/) içinde bir proje oluşturun
   veya mevcut bir projeyi seçin.
2. API Library bölümünden Gmail API'yi etkinleştirin.
3. Google Auth Platform bölümünde uygulama bilgilerini ve OAuth izin ekranını
   yapılandırın. Uygulama test modundaysa gönderecek Gmail hesabını test kullanıcısı
   olarak ekleyin.
4. OAuth Client oluştururken **Web application** türünü seçin. Yetkilendirilmiş
   yönlendirme URI'si olarak tam şu adresi ekleyin:
   `http://127.0.0.1:8000/gmail/auth/callback`
5. İndirilen istemci JSON dosyasını proje içinde
   `credentials/client_secret.json` konumuna koyun.
6. Farklı bir konum veya port kullanacaksanız `.env` içindeki
   `GMAIL_CLIENT_SECRET_PATH`, `GMAIL_REDIRECT_URI` ve `FRONTEND_URL` değerlerini
   güncelleyin. Google Cloud'daki yönlendirme URI'si `.env` ile birebir aynı olmalıdır.
7. FastAPI ve Streamlit'i başlatın. Yeni Başvuru sayfasındaki **Gmail hesabını
   bağla** düğmesine basın, Google onayını tamamlayın ve uygulamaya dönün.

Uygulama Gmail için yalnızca gönderme kapsamını ister; gelen kutusunu okumaz.
Bağlantılı hesap adresini göstermek için ayrıca standart OpenID/e-posta kimlik
izinleri kullanılır. İstemci sırrı, erişim/yenileme tokenları ve bağlı hesap bilgisi
yalnızca yerel `credentials/` klasöründe tutulur ve Git tarafından yok sayılır.

Yerel callback HTTP kullandığı için `.env.example` içinde
`ALLOW_INSECURE_OAUTH_LOOPBACK=true` bulunur. Bu ayar OAuthLib'in HTTP istisnasını
yalnızca `127.0.0.1`, `localhost` veya `::1` callback adreslerinde kaldırır. Public
veya deploy edilmiş bir uygulamada bu ayar kullanılmamalı; callback HTTPS olmalı ve
değer `false` yapılmalıdır. Uygulama public bir HTTP host için bu ayarı reddeder.

## Uygulama kurulumu

Projeyi yeni bilgisayara klonlayın:

```powershell
Set-Location "$env:USERPROFILE\Desktop"
git clone https://github.com/Samervg/job-outreach-assistant.git
cd job-outreach-assistant
```

Sanal ortamı oluşturun, etkinleştirin ve bağımlılıkları kurun:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.venv`, `.env`, SQLite veritabanı ve yüklenen CV dosyaları GitHub'dan gelmez. Yeni bilgisayarda ortam ve kişisel veriler yerel olarak yeniden oluşturulmalıdır.

`.env` içindeki Ollama ayarları:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
GMAIL_CLIENT_SECRET_PATH=credentials/client_secret.json
GMAIL_REDIRECT_URI=http://127.0.0.1:8000/gmail/auth/callback
ALLOW_INSECURE_OAUTH_LOOPBACK=true
FRONTEND_URL=http://localhost:8501
```

Farklı bir model seçerseniz önce modeli indirin ve `.env` değerini aynı adla güncelleyin:

```powershell
ollama pull MODEL_ADI
```

## Uygulamayı çalıştırma

İlk PowerShell terminalinde backend'i başlatın:

```powershell
Set-Location "$env:USERPROFILE\Desktop\job-outreach-assistant"
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --reload
```

İkinci PowerShell terminalinde frontend'i başlatın:

```powershell
Set-Location "$env:USERPROFILE\Desktop\job-outreach-assistant"
.\.venv\Scripts\Activate.ps1
python -m streamlit run frontend\app.py
```

Adresler:

- Streamlit: http://localhost:8501
- FastAPI dokümantasyonu: http://127.0.0.1:8000/docs
- Ollama durumu: http://127.0.0.1:8000/ollama/status
- Gmail durumu: http://127.0.0.1:8000/gmail/status

## Yerel veriler

- SQLite veritabanı: `data/job_outreach.db`
- Aktif CV: `data/uploads/`

Streamlit SQLite'a doğrudan erişmez. Profil, şirket ve taslak işlemleri FastAPI üzerinden yapılır. Veritabanı, yüklemeler ve `.env` Git tarafından yok sayılır.

Gmail gönderiminde taslak, alıcı, konu, metin, aktif CV ve Gmail bağlantısı backend
tarafında yeniden doğrulanır. İstek gövdesinde `confirm_send: true` yoksa gönderim
reddedilir. Başarılı bir taslak tekrar gönderilemez; başarısız bir deneme hata bilgisiyle
kaydedilir ve yalnızca kullanıcı yeniden onay verirse tekrar denenebilir.
