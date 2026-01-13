# YouTube to 3GP/MP3 Converter for Feature Phones

A specialized web application designed to convert YouTube videos into **3GP (video)** and **MP3 (audio)** formats, specifically optimized for older mobile devices (like Nokia, Samsung, etc.) and low-bandwidth networks (2G/3G).

## 🚀 Key Features

- **Format Selection**: Choose between 3GP video or MP3 audio.
- **Search Integration**: Dedicated search bars for finding content directly from the converter pages.
- **Optimized for 2G**: "Ultra Low" quality mode designed to keep a 30-minute video under 14MB.
- **Large File Splitting**: Automatically split large conversions into smaller, manageable parts for easier downloading on basic browsers.
- **Subtitle Burning (Experimental)**: Burn English subtitles directly into 3GP videos for small screens.
- **Lightweight UI**: Minimal CSS, no JavaScript required, designed for browsers like Opera Mini 4.4.
- **Privacy Focused**: No personal data collection or tracking.

## 📊 Quality Presets

### 3GP Video Options (176x144 Resolution)
| Preset | Estimated Size | Specifications | Best For |
| :--- | :--- | :--- | :--- |
| **Ultra Low** | ~2.3 MB / 5 min | 150k video, 10fps, 24kbps AAC | 2G Networks / Nokia 5310 |
| **Low** | ~3.2 MB / 5 min | 200k video, 12fps, 96kbps AAC | Recommended for Feature Phones |
| **Medium** | ~4.6 MB / 5 min | 300k video, 15fps, 256kbps AAC | Better Quality |
| **High** | ~6 MB / 5 min | 400k video, 18fps, 320kbps AAC | Best Quality |

### MP3 Audio Options
| Preset | Estimated Size | Bitrate | Best For |
| :--- | :--- | :--- | :--- |
| **128 kbps** | ~5 MB / 5 min | 128k (Good) | Recommended Balance |
| **192 kbps** | ~7 MB / 5 min | 192k (High) | Music Lovers |
| **256 kbps** | ~9 MB / 5 min | 256k (Very High) | High Fidelity |
| **320 kbps** | ~12 MB / 5 min | 320k (Max) | Studio Quality |

## 🛠 Technical Architecture

- **Web Framework**: [Flask 3.0.0](https://flask.palletsprojects.com/)
- **Download Engine**: [yt-dlp](https://github.com/yt-dlp/yt-dlp) for robust YouTube extraction.
- **Conversion Engine**: [FFmpeg](https://ffmpeg.org/) for high-efficiency encoding.
- **Background Processing**: Multi-threaded conversion with JSON-based status tracking.
- **Storage**: Ephemeral `/tmp` storage with automatic deletion after 6 hours.

## 📦 Installation & Setup

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/your-username/youtube-3gp-converter.git
    cd youtube-3gp-converter
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Ensure System Tools**:
    - FFmpeg must be installed on your system.
    - ImageMagick (optional, for experimental subtitles).

4.  **Run the Application**:
    ```bash
    python app.py
    ```
    The app will be available at `http://localhost:5000`.

## 🛡 Privacy Policy
We do not store any personal information, IP addresses, or browser details. Converted files are deleted automatically from our servers after 6 hours.

## 📩 Contact
For support or suggestions, please contact:
**Email**: [himanshusmartwatch@gmail.com](mailto:himanshusmartwatch@gmail.com)

---
*Note: This tool is intended for personal use and to provide accessibility to users with limited device capabilities.*
