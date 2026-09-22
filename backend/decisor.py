import tkinter as tk
from tkinter import scrolledtext
from datetime import datetime
import paho.mqtt.client as mqtt

BROKER = "broker.hivemq.com"
PORT = 1883

TOPIC_CONDICAO = "grupo5/status/condicao"
TOPIC_CMD_LED = "grupo5/comando/led"
TOPIC_CMD_BUZZER = "grupo5/comando/buzzer"


def decidir(condicao):
    if condicao == "inadequado":
        return ("on", "on")
    elif condicao == "atencao":
        return ("on", "off")
    else: 
        return ("off", "off")


class App:
    def __init__(self, root):
        self.root = root
        root.title("Grupo 5 - Monitor do Backend")
        root.geometry("640x420")
        root.configure(bg="#f3f7f1")

        self.status_var = tk.StringVar(value="Conectando...")
        status_label = tk.Label(
            root, textvariable=self.status_var,
            font=("Segoe UI", 12, "bold"),
            bg="#f3f7f1", fg="#2c5f2d", anchor="w", padx=12, pady=8,
        )
        status_label.pack(fill="x")

        self.log_box = scrolledtext.ScrolledText(
            root, font=("Consolas", 10), bg="white", fg="#1f2d1f",
            state="disabled", wrap="word",
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log_box.tag_config("info", foreground="#5a6b5a")
        self.log_box.tag_config("condicao", foreground="#2c5f2d")
        self.log_box.tag_config("comando", foreground="#8a5a00")
        self.log_box.tag_config("erro", foreground="#b00020")

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

        self.log("Conectando ao broker...", "info")
        try:
            self.client.connect(BROKER, PORT, 60)
            self.client.loop_start()
        except Exception as e:
            self.log(f"Falha ao conectar: {e}", "erro")
            self.status_var.set("Erro de conexao")

    def log(self, mensagem, tag="info"):
        def _inserir():
            hora = datetime.now().strftime("%H:%M:%S")
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{hora}] {mensagem}\n", tag)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.root.after(0, _inserir)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.status_var.set("Conectado ao broker")
            self.log("Conectado ao broker com sucesso.", "info")
            client.subscribe(TOPIC_CONDICAO)
            self.log(f"Assinado em: {TOPIC_CONDICAO}", "info")
        else:
            self.status_var.set(f"Falha na conexao (codigo {rc})")
            self.log(f"Falha na conexao, codigo {rc}", "erro")

    def on_disconnect(self, client, userdata, rc):
        self.status_var.set("Desconectado - tentando reconectar...")
        self.log("Desconectado do broker.", "erro")

    def on_message(self, client, userdata, msg):
        condicao = msg.payload.decode().strip().lower()
        self.log(f"Condicao recebida: {condicao}", "condicao")

        led_cmd, buzzer_cmd = decidir(condicao)
        client.publish(TOPIC_CMD_LED, led_cmd)
        client.publish(TOPIC_CMD_BUZZER, buzzer_cmd)
        self.log(f"  -> Publicado LED: {led_cmd} | BUZZER: {buzzer_cmd}", "comando")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()