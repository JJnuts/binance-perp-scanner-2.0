FROM strategy-lab-vectorbt:0.28.5-phase1

RUN python -m pip install --no-cache-dir --only-binary=:all: \
        streamlit==1.59.2 \
        streamlit-autorefresh==1.0.1 \
    && python -m pip freeze --all > /opt/strategy-lab/phase8-freeze.txt

WORKDIR /repo
ENTRYPOINT ["python"]
