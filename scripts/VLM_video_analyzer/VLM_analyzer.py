import base64
import io
import os
import tempfile

import cv2
import requests
import streamlit as st
from PIL import Image

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3.6:27b"

st.set_page_config(
    page_title="Qwen Video Chat",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 Qwen Video Chat")
st.write(
    "Tölts fel egy videót, és kérdezz róla magyarul "
    "a helyi Ollama modellen keresztül."
)

with st.sidebar:
    st.header("Beállítások")

    ollama_url = st.text_input(
        "Ollama címe",
        value=OLLAMA_URL
    ).rstrip("/")

    model_name = st.text_input(
        "Modell neve",
        value=DEFAULT_MODEL
    )

    frame_interval = st.slider(
        "Képkockák közötti idő (másodperc)",
        min_value=1,
        max_value=30,
        value=1
    )

    max_frames = st.slider(
        "Maximális képkockaszám",
        min_value=2,
        max_value=60,
        value=25
    )

    st.caption(
        "A modell a kiválasztott képkockákat elemzi. "
        "A videó hangját ez a változat nem dolgozza fel."
    )



def extract_frames(video_bytes, suffix, interval, limit):
    """
    Képkockákat vesz ki a videóból.
    Visszaadja az időpontokat, a képek Base64 kódját,
    a megjeleníthető képeket és a videó hosszát.
    """

    temp_path = None
    cap = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        ) as temp_file:
            temp_file.write(video_bytes)
            temp_path = temp_file.name

        cap = cv2.VideoCapture(temp_path)

        if not cap.isOpened():
            raise ValueError(
                "A videó nem nyitható meg. "
                "Próbálj egy MP4 fájlt."
            )

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)

        if not fps or fps <= 0:
            raise ValueError(
                "Nem sikerült meghatározni a videó képkockasebességét."
            )

        duration = frame_count / fps

        if duration <= 0:
            raise ValueError("A videó hossza nem állapítható meg.")

        timestamps = list(
            range(0, max(1, int(duration)), interval)
        )

        # A teljes videóból választunk mintákat.
        if len(timestamps) > limit:
            indexes = [
                round(i * (len(timestamps) - 1) / (limit - 1))
                for i in range(limit)
            ]

            timestamps = [
                timestamps[index]
                for index in indexes
            ]

        frames = []

        for seconds in timestamps:
            cap.set(
                cv2.CAP_PROP_POS_MSEC,
                seconds * 1000
            )

            success, frame = cap.read()

            if not success:
                continue

            frame = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB
            )

            image = Image.fromarray(frame)

            image.thumbnail((1024, 1024))

            buffer = io.BytesIO()

            image.save(
                buffer,
                format="JPEG",
                quality=80
            )

            image_base64 = base64.b64encode(
                buffer.getvalue()
            ).decode("utf-8")

            frames.append({
                "time": seconds,
                "base64": image_base64,
                "image": image.copy()
            })

        if not frames:
            raise ValueError(
                "Nem sikerült képkockákat kinyerni."
            )

        return frames, duration

    finally:
        if cap is not None:
            cap.release()

        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)



def ask_ollama(url, model, question, frames, duration, history):
    """
    Elküldi a videó képkockáit és a kérdést az Ollamának.
    """

    system_message = (
        "Magyar nyelvű videóelemző asszisztens vagy. "
        "A felhasználó által feltett kérdésekre a megadott "
        "videóképkockák alapján válaszolj. "
        "Ne találj ki olyan eseményeket, amelyek nem láthatók. "
        "Ha egy információ nem állapítható meg, ezt mondd ki. "
        "A képkockák időpontját használd az események "
        "időbeli elhelyezéséhez."
    )

    frame_description = "\n".join(
        f"A(z) {frame['time']} másodpercnél készült képkocka."
        for frame in frames
    )

    prompt = (
        f"A videó hossza körülbelül {duration:.1f} másodperc.\n"
        f"A videóból kiválasztott képkockák:\n"
        f"{frame_description}\n\n"
        f"A felhasználó kérdése: {question}"
    )

    messages = [
        {
            "role": "system",
            "content": system_message
        }
    ]

    # Az előző kérdések és válaszok megőrzése.
    for item in history[-6:]:
        messages.append({
            "role": item["role"],
            "content": item["content"]
        })

    # A képkockák és az aktuális kérdés elküldése.
    messages.append({
        "role": "user",
        "content": prompt,
        "images": [
            frame["base64"]
            for frame in frames
        ]
    })

    response = requests.post(
        f"{url}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False
        },
        timeout=600
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Ollama API hiba: {response.status_code}\n"
            f"{response.text[:1000]}"
        )

    result = response.json()

    return result.get(
        "message", {}
    ).get(
        "content",
        "A modell nem adott választ."
    )


uploaded_file = st.file_uploader(
    "Válassz egy videófájlt",
    type=["mp4", "mov", "mkv", "avi", "webm"]
)

if uploaded_file is not None:

    video_bytes = uploaded_file.getvalue()

    st.video(video_bytes)

    if st.button(
        "Videó feldolgozása",
        type="primary"
    ):

        with st.spinner(
            "Képkockák kinyerése a videóból..."
        ):

            try:
                frames, duration = extract_frames(
                    video_bytes,
                    os.path.splitext(uploaded_file.name)[1] or ".mp4",
                    frame_interval,
                    max_frames
                )

                st.session_state["video_frames"] = frames
                st.session_state["video_duration"] = duration
                st.session_state["video_name"] = uploaded_file.name
                st.session_state["chat_history"] = []

                st.success(
                    f"Sikeres feldolgozás. "
                    f"Videóhossz: {duration:.1f} másodperc. "
                    f"Képkockák száma: {len(frames)}."
                )

            except Exception as error:
                st.error(
                    f"Hiba történt a videó feldolgozásakor: {error}"
                )


if st.session_state.get("video_frames"):

    frames = st.session_state["video_frames"]

    st.subheader("Kiválasztott képkockák")

    columns = st.columns(
        min(4, len(frames))
    )

    for index, frame in enumerate(frames):
        with columns[index % len(columns)]:
            st.image(
                frame["image"],
                caption=f"{frame['time']} másodperc",
                use_container_width=True
            )

    st.divider()

    st.subheader("Kérdezz a videóról")

    history = st.session_state.setdefault(
        "chat_history", []
    )

    # Korábbi beszélgetés megjelenítése.
    for item in history:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

    question = st.chat_input(
        "Például: Mi történik a videóban?"
    )

    if question:

        with st.chat_message("user"):
            st.markdown(question)

        try:
            with st.chat_message("assistant"):

                with st.spinner(
                    f"A {model_name} modell elemzi a videót..."
                ):

                    answer = ask_ollama(
                        ollama_url,
                        model_name,
                        question,
                        frames,
                        st.session_state["video_duration"],
                        history
                    )

                    st.markdown(answer)

            history.append({
                "role": "user",
                "content": question
            })

            history.append({
                "role": "assistant",
                "content": answer
            })

        except requests.exceptions.ConnectionError:
            st.error(
                "Nem érhető el az Ollama. "
                "Ellenőrizd, hogy fut-e a program, "
                "és helyes-e a címe."
            )

        except requests.exceptions.Timeout:
            st.error(
                "A modell túl sokáig dolgozott. "
                "Próbálj kevesebb képkockát küldeni."
            )

        except Exception as error:
            st.error(
                f"Hiba történt a modell hívásakor: {error}"
            )

else:
    st.info(
        "Tölts fel egy videót, majd kattints "
        "a Videó feldolgozása gombra."
    )
