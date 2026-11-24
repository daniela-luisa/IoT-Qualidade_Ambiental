CREATE TABLE dispositivos (
    id              text PRIMARY KEY CHECK (id ~ '^estacao-[0-9]{2}$'),
    nome            text NOT NULL,
    online          boolean NOT NULL DEFAULT false,
    modo            text CHECK (modo IN ('automatico', 'manual', 'seguro')),
    condicao        text CHECK (condicao IN ('adequado', 'atencao', 'inadequado')),
    fw              text,
    ultimo_estado   jsonb,
    recursos        jsonb,
    ultimo_contato  timestamptz,
    criado_em       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE leituras (
    id              bigserial PRIMARY KEY,
    dispositivo_id  text NOT NULL REFERENCES dispositivos (id),
    grandeza        text NOT NULL CHECK (grandeza IN ('temperatura', 'umidade_ar', 'umidade_solo', 'luminosidade')),
    valor           double precision NOT NULL,
    unidade         text,
    medido_em       timestamptz NOT NULL,
    recebido_em     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_leituras_dispositivo_grandeza_tempo
    ON leituras (dispositivo_id, grandeza, medido_em DESC);

CREATE TABLE usuarios (
    id          serial PRIMARY KEY,
    email       text NOT NULL UNIQUE,
    senha_hash  text NOT NULL,
    papel       text NOT NULL CHECK (papel IN ('operador', 'admin')),
    criado_em   timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE comando_seq;

CREATE TABLE comandos (
    comando_id      text PRIMARY KEY,
    dispositivo_id  text NOT NULL REFERENCES dispositivos (id),
    alvo            text NOT NULL CHECK (alvo IN ('bomba', 'led', 'buzzer', 'config')),
    acao            text NOT NULL,
    duracao_s       integer,
    parametros      jsonb,
    origem          text NOT NULL CHECK (origem IN ('usuario', 'regra')),
    usuario_id      integer REFERENCES usuarios (id),
    estado          text NOT NULL DEFAULT 'enviado'
                    CHECK (estado IN ('enviado', 'recebido', 'executado', 'confirmado',
                                      'rejeitado', 'falhou', 'expirado')),
    motivo          text,
    enviado_em      timestamptz NOT NULL DEFAULT now(),
    recebido_em     timestamptz,
    executado_em    timestamptz,
    confirmado_em   timestamptz,
    atualizado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_comandos_dispositivo_tempo
    ON comandos (dispositivo_id, enviado_em DESC);

CREATE TABLE regras (
    dispositivo_id   text PRIMARY KEY REFERENCES dispositivos (id),
    grandeza         text NOT NULL DEFAULT 'umidade_solo'
                     CHECK (grandeza IN ('temperatura', 'umidade_ar', 'umidade_solo', 'luminosidade')),
    limiar           double precision NOT NULL,
    janela_min       integer NOT NULL CHECK (janela_min > 0),
    intervalo_min_s  integer NOT NULL CHECK (intervalo_min_s > 0),
    duracao_s        integer NOT NULL CHECK (duracao_s BETWEEN 1 AND 15),
    ativa            boolean NOT NULL DEFAULT true,
    atualizado_em    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE alertas (
    id              bigserial PRIMARY KEY,
    dispositivo_id  text NOT NULL REFERENCES dispositivos (id),
    tipo            text NOT NULL CHECK (tipo IN ('dispositivo_offline', 'solo_seco', 'reservatorio_vazio')),
    mensagem        text,
    aberto_em       timestamptz NOT NULL DEFAULT now(),
    resolvido_em    timestamptz
);
CREATE UNIQUE INDEX uq_alerta_ativo
    ON alertas (dispositivo_id, tipo) WHERE resolvido_em IS NULL;

CREATE TABLE eventos (
    id              bigserial PRIMARY KEY,
    dispositivo_id  text REFERENCES dispositivos (id),
    tipo            text NOT NULL,
    detalhe         text,
    criado_em       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_eventos_tempo ON eventos (criado_em DESC);

INSERT INTO dispositivos (id, nome) VALUES
    ('estacao-01', 'Estacao 01'),
    ('estacao-02', 'Estacao 02');

INSERT INTO regras (dispositivo_id, grandeza, limiar, janela_min, intervalo_min_s, duracao_s) VALUES
    ('estacao-01', 'umidade_solo', 30, 5, 60, 3),
    ('estacao-02', 'umidade_solo', 30, 5, 60, 3);
