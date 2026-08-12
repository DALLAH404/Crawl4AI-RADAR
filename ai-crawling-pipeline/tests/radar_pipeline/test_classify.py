"""Tests for the classification rules module."""

import pytest
from radar_pipeline.classify.rules import (
    classificar,
    classify_article,
    eh_aftermarket_real,
    eh_fora_de_escopo,
    eh_lancamento,
)


class TestOutOfScope:
    def test_sports_is_out_of_scope(self):
        assert eh_fora_de_escopo("Campeonato de futebol tem novo líder")

    def test_food_is_out_of_scope(self):
        assert eh_fora_de_escopo("Novo sabor de sorvete chega ao mercado")

    def test_entertainment_is_out_of_scope(self):
        assert eh_fora_de_escopo("Novo filme estreia no cinema Netflix anuncia nova série")

    def test_auto_content_is_not_out_of_scope(self):
        assert not eh_fora_de_escopo("Bosch lança nova linha de freios para aftermarket")

    def test_competitor_news_is_not_out_of_scope(self):
        assert not eh_fora_de_escopo("Valeo anuncia aquisição de planta no Brasil")


class TestLaunchDetection:
    def test_launch_with_part_keyword(self):
        assert eh_lancamento("Bosch lança nova linha de pastilhas de freio")

    def test_launch_without_part_keyword(self):
        assert eh_lancamento("Empresa lança nova plataforma digital")

    def test_not_a_launch(self):
        assert not eh_lancamento("Bosch anuncia resultados trimestrais")


class TestAftermarketReal:
    def test_aftermarket_part_is_real(self):
        assert eh_aftermarket_real("Nova linha de pastilhas de freio e amortecedores")

    def test_corporate_without_parts(self):
        assert not eh_aftermarket_real("Empresa anuncia novo CEO e reestruturação")


class TestClassificar:
    def test_launch_with_part(self):
        tipo, nivel = classificar(
            "Bosch lança nova linha de pastilhas de freio para o mercado brasileiro"
        )
        assert tipo == "Lancamento"
        assert nivel == "Alto"

    def test_launch_without_part_is_institucional(self):
        tipo, nivel = classificar(
            "Bosch lança novo site e plataforma de e-commerce"
        )
        assert tipo == "Institucional"
        assert nivel == "Medio"

    def test_aquisicao_is_ma_alto(self):
        tipo, nivel = classificar(
            "ZF anuncia aquisição da divisão de freios da Wabco"
        )
        assert tipo == "M&A"
        assert nivel == "Alto"

    def test_investimento_is_alto(self):
        tipo, nivel = classificar(
            "Schaeffler investe R$ 200 milhões em nova fábrica no Paraná"
        )
        assert tipo == "Investimento"
        assert nivel == "Alto"

    def test_expansao_is_medio(self):
        tipo, nivel = classificar(
            "Denso anuncia expansão da produção no Brasil"
        )
        assert tipo == "Investimento"
        assert nivel == "Medio"

    def test_palestra_is_baixo(self):
        tipo, nivel = classificar(
            "Sindipeças promove workshop sobre tendências do aftermarket"
        )
        assert tipo == "Evento"
        assert nivel == "Baixo"

    def test_selic_is_indicador_medio(self):
        tipo, nivel = classificar(
            "Copom decide manter Selic em 10.5% ao ano"
        )
        assert tipo == "Indicador"
        assert nivel == "Medio"

    def test_fallback_is_atualizacao_baixo(self):
        tipo, nivel = classificar(
            "Empresa divulga relatório de sustentabilidade anual"
        )
        assert tipo == "Atualizacao"
        assert nivel == "Baixo"

    def test_launch_respects_priority_over_temas_kw(self):
        tipo, nivel = classificar(
            "Marelli lança novo farol LED e promove workshop para reparadores"
        )
        assert tipo == "Lancamento"
        assert nivel == "Alto"


class TestClassifyArticle:
    def test_out_of_scope_is_filtered(self):
        event_type, alert_level, is_launch = classify_article(
            "Campeonato de futebol tem resultados", "Times disputam vaga"
        )
        assert event_type == "Atualizacao"
        assert not is_launch

    def test_valid_article(self):
        event_type, alert_level, is_launch = classify_article(
            "Bosch lança nova linha de freios para veículos pesados",
            "A Bosch anunciou o lançamento de uma nova linha de pastilhas"
        )
        assert event_type == "Lancamento"
        assert alert_level == "Alto"
        assert is_launch
