import os
import sys
import json
import time
import wave
import io
import threading
import winsound
import numpy as np
import sounddevice as sd
import requests
import pyperclip
from pynput import keyboard

# Carrega configurações
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    default_config = {
        "whisper_url": "http://100.126.30.60:8005/v1/audio/transcriptions",
        "model": "Systran/faster-whisper-base",
        "language": "pt",
        "hotkey": "F8",
        "auto_paste": True,
        "copy_to_clipboard": True,
        "sound_feedback": True,
        "sample_rate": 16000,
        "channels": 1
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                default_config.update(loaded)
        except Exception as e:
            print(f"[AVISO] Erro ao carregar config.json: {e}. Usando padrões.")
    return default_config

config = load_config()

class WhisperDictateClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.is_recording = False
        self.audio_frames = []
        self.stream = None
        self.lock = threading.Lock()
        self.controller = keyboard.Controller()

    def play_sound(self, sound_type):
        if not self.cfg.get("sound_feedback", True):
            return
        def _play():
            try:
                if sound_type == "start":
                    winsound.Beep(900, 120)
                elif sound_type == "stop":
                    winsound.Beep(650, 100)
                elif sound_type == "success":
                    winsound.Beep(1100, 150)
                elif sound_type == "error":
                    winsound.Beep(350, 300)
            except Exception:
                pass
        threading.Thread(target=_play, daemon=True).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[ÁUDIO STATUS]: {status}", file=sys.stderr)
        if self.is_recording:
            self.audio_frames.append(indata.copy())

    def start_recording(self):
        with self.lock:
            if self.is_recording:
                return
            self.audio_frames = []
            self.is_recording = True
            
        print("\n🔴 [GRAVANDO] Fale agora... (Pressione F8 novamente para parar)")
        self.play_sound("start")

        try:
            self.stream = sd.InputStream(
                samplerate=self.cfg.get("sample_rate", 16000),
                channels=self.cfg.get("channels", 1),
                dtype='int16',
                callback=self._audio_callback
            )
            self.stream.start()
        except Exception as e:
            print(f"❌ [ERRO] Falha ao iniciar microfone: {e}")
            self.is_recording = False
            self.play_sound("error")

    def stop_recording(self):
        with self.lock:
            if not self.is_recording:
                return
            self.is_recording = False

        print("⏹️ [PARANDO] Finalizando gravação...")
        self.play_sound("stop")

        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None
        except Exception as e:
            print(f"[AVISO] Erro ao fechar stream: {e}")

        # Processa áudio em thread separada para não travar o listener de teclas
        threading.Thread(target=self._process_and_transcribe, daemon=True).start()

    def toggle_recording(self):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def _process_and_transcribe(self):
        if not self.audio_frames:
            print("⚠️ [AVISO] Nenhum dado de áudio capturado.")
            return

        print("⏳ [PROCESSANDO] Preparando áudio e enviando para o Whisper...")
        start_time = time.time()

        try:
            # Concatena frames
            audio_data = np.concatenate(self.audio_frames, axis=0)
            
            # Valida duração mínima (~0.5s)
            duration_sec = len(audio_data) / self.cfg.get("sample_rate", 16000)
            if duration_sec < 0.4:
                print(f"⚠️ [AVISO] Gravação muito curta ({duration_sec:.1f}s), ignorada.")
                return

            # Cria arquivo WAV em memória
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(self.cfg.get("channels", 1))
                wf.setsampwidth(2) # 16-bit
                wf.setframerate(self.cfg.get("sample_rate", 16000))
                wf.writeframes(audio_data.tobytes())
            
            wav_buffer.seek(0)
            
            # Prepara payload multipart/form-data
            files = {
                "file": ("audio.wav", wav_buffer, "audio/wav")
            }
            data = {
                "model": self.cfg.get("model", "Systran/faster-whisper-base"),
                "response_format": "json"
            }
            if self.cfg.get("language"):
                data["language"] = self.cfg.get("language")

            url = self.cfg.get("whisper_url", "http://100.126.30.60:8005/v1/audio/transcriptions")
            
            # Envia requisição POST
            resp = requests.post(url, files=files, data=data, timeout=30)
            
            if resp.status_code == 200:
                result = resp.json()
                transcribed_text = result.get("text", "").strip()
                elapsed = time.time() - start_time
                
                if transcribed_text:
                    print(f"✅ [SUCESSO ({elapsed:.1f}s)]: \"{transcribed_text}\"")
                    self.play_sound("success")
                    
                    # Copia para o clipboard
                    if self.cfg.get("copy_to_clipboard", True):
                        pyperclip.copy(transcribed_text)
                    
                    # Digita automaticamente no cursor
                    if self.cfg.get("auto_paste", True):
                        time.sleep(0.08) # Pequena folga para a janela alvo manter o foco
                        # Simula Ctrl+V
                        self.controller.press(keyboard.Key.ctrl)
                        self.controller.press('v')
                        self.controller.release('v')
                        self.controller.release(keyboard.Key.ctrl)
                else:
                    print(f"ℹ️ [INFO ({elapsed:.1f}s)] Nenhuma fala detectada.")
            else:
                print(f"❌ [ERRO API {resp.status_code}]: {resp.text}")
                self.play_sound("error")

        except Exception as e:
            print(f"❌ [ERRO TRANSCRIÇÃO]: {e}")
            self.play_sound("error")

        print("🟢 [PRONTO] Aguardando F8 para gravar...")

def main():
    client = WhisperDictateClient(config)
    target_key = config.get("hotkey", "F8").upper()

    print("=" * 60)
    print("🎙️  WHISPER DICTATE PC - CLIENTE DE VOZ PARA WINDOWS")
    print("=" * 60)
    print(f"🌐 Servidor Whisper: {config.get('whisper_url')}")
    print(f"⌨️  Atalho configurado: {target_key}")
    print(f"📋 Copiar para Área de Transferência: {config.get('copy_to_clipboard')}")
    print(f"✍️  Auto-digitar no cursor: {config.get('auto_paste')}")
    print(f"🔔 Feedback Sonoro: {config.get('sound_feedback')}")
    print("=" * 60)
    print("🟢 [PRONTO] Pressione F8 para iniciar a gravação!")
    print("   (Para sair, pressione Ctrl + C no terminal)\n")

    def on_press(key):
        try:
            # Verifica teclas de função (F1 - F12)
            if hasattr(key, 'name') and key.name.upper() == target_key:
                client.toggle_recording()
            elif hasattr(keyboard.Key, target_key.lower()) and key == getattr(keyboard.Key, target_key.lower()):
                client.toggle_recording()
        except Exception as e:
            print(f"[ERRO LISTENER]: {e}")

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

if __name__ == "__main__":
    main()
