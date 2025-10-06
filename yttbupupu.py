# youtube_streaming_large_upload.py
import sys
import subprocess
import threading
import os
import time
import socket
import platform

# Install streamlit jika belum ada
try:
    import streamlit as st
except Exception:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "streamlit"])
    import streamlit as st

# Install Flask jika belum ada (digunakan untuk upload file besar via halaman upload terpisah)
try:
    from flask import Flask, request, render_template_string
    from werkzeug.utils import secure_filename
except Exception:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask"])
    from flask import Flask, request, render_template_string
    from werkzeug.utils import secure_filename

# -------------------------
# Helper: detect local IP
# -------------------------
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # connect ke public DNS untuk mengambil ip lokal tanpa mengirim paket
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# -------------------------
# Mini Flask upload server
# -------------------------
def start_upload_server(upload_folder, host="0.0.0.0", port=8000):
    """
    Start a small Flask server (in a background thread) that serves a simple upload page.
    Files uploaded here are saved into upload_folder.
    """
    app = Flask(__name__)
    os.makedirs(upload_folder, exist_ok=True)

    INDEX_HTML = """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8"/>
        <title>Upload Video (Large File)</title>
      </head>
      <body>
        <h2>Upload Video — unggah file besar langsung ke server</h2>
        <p>File akan disimpan ke folder aplikasi pada server.</p>
        <input id="fileinput" type="file" />
        <br/><br/>
        <button onclick="upload()">Upload</button>
        <div id="progress"></div>
        <div id="status"></div>
        <script>
        function upload(){
          var f = document.getElementById('fileinput').files[0];
          if(!f){ alert('Pilih file dulu'); return; }
          var xhr = new XMLHttpRequest();
          xhr.upload.addEventListener('progress', function(e){
            if(e.lengthComputable){
              var p = (e.loaded / e.total * 100).toFixed(2);
              document.getElementById('progress').innerText = 'Progress: ' + p + '%';
            }
          });
          xhr.onreadystatechange = function(){
            if(xhr.readyState==4){
              document.getElementById('status').innerText = xhr.responseText;
            }
          }
          xhr.open('POST', '/upload', true);
          var fd = new FormData();
          fd.append('file', f);
          xhr.send(fd);
        }
        </script>
      </body>
    </html>
    """

    @app.route("/", methods=["GET"])
    def index():
        return render_template_string(INDEX_HTML)

    @app.route("/upload", methods=["POST"])
    def upload():
        if 'file' not in request.files:
            return "No file part", 400
        f = request.files['file']
        if f.filename == '':
            return "No selected file", 400
        filename = secure_filename(f.filename)
        dst = os.path.join(upload_folder, filename)

        # save in chunks to avoid loading whole file in memory
        try:
            with open(dst, "wb") as out:
                chunk = f.stream.read(4096)
                while chunk:
                    out.write(chunk)
                    chunk = f.stream.read(4096)
            return f"Uploaded: {filename}"
        except Exception as e:
            return f"Error saving file: {e}", 500

    def run():
        # disable flask log on console to keep Streamlit clean
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        try:
            app.run(host=host, port=port, debug=False, use_reloader=False)
        except Exception as e:
            print(f"[Upload server] gagal start: {e}")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return f"http://{get_local_ip()}:{port}/"

# -------------------------
# FFmpeg streaming
# -------------------------
def run_ffmpeg_process(video_path, stream_key, is_shorts=False, loop=False, log_callback=print):
    """
    Start ffmpeg as subprocess and stream to YouTube RTMP.
    Returns subprocess.Popen object.
    """
    output_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    scale_args = ["-vf", "scale=720:1280"] if is_shorts else []

    cmd = ["ffmpeg", "-re"]
    if loop:
        # loop file indefinitely (-stream_loop -1 must be before -i)
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", video_path,
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", "2500k",
            "-maxrate", "2500k", "-bufsize", "5000k",
            "-g", "60", "-keyint_min", "60",
            "-c:a", "aac", "-b:a", "128k"]
    if scale_args:
        cmd += scale_args
    cmd += ["-f", "flv", output_url]

    log_callback("Menjalankan ffmpeg: " + " ".join(cmd))
    # Start process
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # start a thread to read and forward process output
    def stream_reader():
        try:
            for line in proc.stdout:
                log_callback(line.rstrip())
        except Exception as e:
            log_callback(f"[ffmpeg read error] {e}")
    threading.Thread(target=stream_reader, daemon=True).start()
    return proc

# -------------------------
# STREAMLIT APP
# -------------------------
def main():
    st.set_page_config(page_title="YouTube Live (Large Uploads)", page_icon="🎥", layout="wide")

    st.title("YouTube Live — dukung upload video besar (1 jam+)")

    # NOTE: If you want to use Streamlit's built-in uploader for big files,
    # you must set STREAMLIT_SERVER_MAX_UPLOAD_SIZE before starting Streamlit.
    st.info(
        "Untuk upload via browser ke Streamlit (metode built-in), jalankan Streamlit dengan:\n\n"
        "`STREAMLIT_SERVER_MAX_UPLOAD_SIZE=4096 streamlit run youtube_streaming_large_upload.py`\n\n"
        "Atau gunakan tombol 'Open large upload page' di bawah (direkomendasikan untuk file besar)."
    )

    # Start background Flask upload server once
    if 'upload_server_url' not in st.session_state:
        upload_folder = os.path.abspath(".")
        try:
            upload_url = start_upload_server(upload_folder, host="0.0.0.0", port=8000)
            st.session_state['upload_server_url'] = upload_url
            st.session_state['upload_server_started'] = True
        except Exception as e:
            st.session_state['upload_server_started'] = False
            st.session_state['upload_server_url'] = None
            st.error(f"Gagal mulai upload server: {e}")

    col1, col2 = st.columns([2,1])

    with col1:
        st.header("1) Pilih sumber video")
        # show files in current directory (mp4/flv)
        video_files = [f for f in os.listdir('.') if f.lower().endswith(('.mp4', '.flv', '.mkv', '.mov'))]
        st.write("File video di folder aplikasi:")
        selected = st.selectbox("Pilih file lokal (jika file sudah ada di server)", ["(tidak memilih)"] + video_files)

        st.write("---")
        st.write("Atau: Masukkan path file lokal (absolute path) di server:")
        local_path_input = st.text_input("Path file lokal (contoh: /home/user/video.mp4)")

        st.write("---")
        st.write("Atau: Upload via Streamlit (jika server sudah dijalankan dengan batas upload besar)")
        st.write("**Perhatian**: st.file_uploader tetap dibatasi oleh konfigurasi Streamlit.")
        uploaded_file = st.file_uploader("Upload file (mp4/flv) — bila berukuran besar gunakan 'Large upload page' di kanan", type=['mp4','flv','mkv','mov'])

        # show link to upload server
        st.write("---")
        st.header("Upload file besar (direkomendasikan)")
        if st.session_state.get('upload_server_started'):
            upload_url = st.session_state['upload_server_url']
            st.markdown(f"[Open large upload page]({upload_url}){{:target=\"_blank\"}}", unsafe_allow_html=True)
            st.write(f"Atau buka: {upload_url} di browser. Jika server berjalan di mesin remote, gunakan IP server di URL.")
        else:
            st.error("Upload server belum berjalan. Cek log.")

    with col2:
        st.header("2) Stream settings")
        stream_key = st.text_input("YouTube Stream Key", type="password")
        is_shorts = st.checkbox("Mode Shorts (720x1280)", value=False)
        loop_video = st.checkbox("Loop video (stream terus menerus)", value=False)

        st.write("---")
        st.header("Kontrol ffmpeg")
        start_btn = st.button("🚀 Mulai Streaming")
        stop_btn = st.button("🛑 Stop Streaming")

    # Determine video_path from choices
    video_path = None
    if selected and selected != "(tidak memilih)":
        video_path = os.path.abspath(selected)
    if local_path_input:
        if os.path.exists(local_path_input):
            video_path = local_path_input
        else:
            st.warning("Path lokal tidak ditemukan di server.")
    if uploaded_file is not None:
        # save uploaded file in chunks to disk to avoid memory blow
        save_to = os.path.abspath(uploaded_file.name)
        try:
            with open(save_to, "wb") as out:
                # uploaded_file supports read() - read in chunks
                while True:
                    chunk = uploaded_file.read(4096)
                    if not chunk:
                        break
                    out.write(chunk)
            st.success(f"✅ File disimpan sebagai: {save_to}")
            video_path = save_to
        except Exception as e:
            st.error(f"Gagal menyimpan file upload: {e}")

    st.write("---")
    st.header("Status")
    status_placeholder = st.empty()

    # Logging list
    if 'logs' not in st.session_state:
        st.session_state['logs'] = []

    def log(msg):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        st.session_state['logs'].append(f"[{timestamp}] {msg}")
        # keep last 200 lines
        st.session_state['logs'] = st.session_state['logs'][-200:]
        status_placeholder.text("\n".join(st.session_state['logs'][-20:]))

    # Manage ffmpeg subprocess
    if 'ffmpeg_proc' not in st.session_state:
        st.session_state['ffmpeg_proc'] = None

    if start_btn:
        if not video_path:
            st.error("Pilih atau upload video dulu (lihat opsi di kiri).")
        elif not stream_key:
            st.error("Masukkan YouTube stream key.")
        else:
            # start ffmpeg
            try:
                proc = run_ffmpeg_process(video_path, stream_key, is_shorts=is_shorts, loop=loop_video, log_callback=log)
                st.session_state['ffmpeg_proc'] = proc
                st.success("Streaming dimulai — lihat log untuk output ffmpeg.")
            except Exception as e:
                st.error(f"Gagal jalankan ffmpeg: {e}")
                log(f"Gagal jalankan ffmpeg: {e}")

    if stop_btn:
        proc = st.session_state.get('ffmpeg_proc')
        if proc and proc.poll() is None:
            log("Menghentikan proses ffmpeg...")
            try:
                proc.terminate()
                time.sleep(1)
                if proc.poll() is None:
                    proc.kill()
                st.session_state['ffmpeg_proc'] = None
                log("ffmpeg dihentikan.")
                st.success("ffmpeg dihentikan.")
            except Exception as e:
                log(f"Gagal hentikan ffmpeg: {e}")
                st.error(f"Gagal hentikan ffmpeg: {e}")
        else:
            # fallback: try to kill any ffmpeg processes (platform dependent)
            try:
                if platform.system() == "Windows":
                    subprocess.Popen("taskkill /im ffmpeg.exe /f", shell=True)
                else:
                    subprocess.Popen("pkill ffmpeg", shell=True)
                log("Menjalankan perintah kill ffmpeg (fallback).")
                st.warning("Mencoba hentikan ffmpeg lewat perintah sistem.")
            except Exception as e:
                log(f"Gagal melakukan pkill: {e}")
                st.error(f"Gagal hentikan ffmpeg: {e}")

    # Show last log lines
    status_placeholder.text("\n".join(st.session_state.get('logs', [])[-20:]))

    # Show currently available video files
    st.write("---")
    st.write("Video yang tersedia di folder aplikasi:")
    st.write([f for f in os.listdir('.') if f.lower().endswith(('.mp4','.flv','.mkv','.mov'))])

    st.write("---")
    st.caption("Catatan: Upload server sederhana ini tidak memakai autentikasi. Jangan jalankan di publik tanpa proteksi. " 
               "Jika server dijalankan di VPS publik, pastikan port 8000 dibatasi atau gunakan VPN/SSH.")

if __name__ == "__main__":
    main()
