# Job Outreach Assistant — Phase 4

Yerelde çalışan, tek kullanıcıya yönelik iş başvurusu iletişim asistanı MVP'si.

Mevcut özellikler:

- Profil ve PDF CV yönetimi
- Manuel şirket yönetimi
- Yerel Ollama modeliyle kişiselleştirilmiş e-posta taslağı üretimi
- Taslakları görüntüleme, düzenleme ve SQLite'a kaydetme
- Güvenli SQLite bağlantı ve transaction yönetimi

Bu faz e-posta göndermez. Gmail, otomatik outreach, scraping ve şirket keşfi henüz bulunmaz.

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

## Yerel veriler

- SQLite veritabanı: `data/job_outreach.db`
- Aktif CV: `data/uploads/`

Streamlit SQLite'a doğrudan erişmez. Profil, şirket ve taslak işlemleri FastAPI üzerinden yapılır. Veritabanı, yüklemeler ve `.env` Git tarafından yok sayılır.
