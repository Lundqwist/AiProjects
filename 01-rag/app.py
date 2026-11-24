"""Chat UI for the RAG document assistant. Run: streamlit run app.py"""
import tempfile
from pathlib import Path

import streamlit as st

import rag

st.set_page_config(page_title="Document assistant", page_icon=":books:")
st.title("Document assistant")
st.caption(f"Local RAG: {rag.EMBED_MODEL} + ChromaDB + {rag.CHAT_MODEL} via Ollama")

with st.sidebar:
    st.metric("Chunks in database", rag.collection().count())
    files = st.file_uploader("Add documents", type=["pdf", "md", "txt"], accept_multiple_files=True)
    if files and st.button("Ingest"):
        with tempfile.TemporaryDirectory() as tmp:
            for f in files:
                path = Path(tmp) / f.name
                path.write_bytes(f.getvalue())
                with st.spinner(f"Embedding {f.name}..."):
                    n = rag.ingest_file(path, Path(tmp))
                st.success(f"{f.name}: {n} chunks")
        st.rerun()

if "history" not in st.session_state:
    st.session_state.history = []

for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if question := st.chat_input("Ask about your documents"):
    st.session_state.history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        hits = rag.search(question)
        answer = st.write_stream(rag.answer_stream(question, hits))
        if hits:
            with st.expander("Sources"):
                for i, h in enumerate(hits, 1):
                    st.markdown(f"**[{i}] {h.label}** (distance {h.distance:.2f})")
                    st.text(h.text[:500])
    st.session_state.history.append({"role": "assistant", "content": answer})
