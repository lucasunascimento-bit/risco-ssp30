package br.com.mercadolivre.lp.ssp30;

// ============================================================
// RiscoSSP30.java  — Plano 90 dias: Semanas 1-2
//
// Objetivo de aprendizado:
//   - Estrutura de um projeto Java (package, imports, classes)
//   - Tipos primitivos e String vs objetos
//   - Constantes (static final)
//   - Métodos estáticos e de instância
//   - Coleções: List, Map
//   - Javadoc básico
//
// Para compilar: javac RiscoSSP30.java
// Para rodar:    java br.com.mercadolivre.lp.ssp30.RiscoSSP30
// ============================================================

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * Espelho em Java do script atualizacao_risco.py.
 *
 * <p>Por enquanto é um esqueleto — cada método tem a lógica descrita
 * em comentários. A implementação real usará BigQuery client para Java
 * e Google Sheets API (semanas 3-4 com Spring Boot).
 */
public class RiscoSSP30 {

    // ====================================================
    // CONSTANTES  (equivalente às variáveis no topo do .py)
    // Em Java: static final = imutável e pertence à classe
    // ====================================================

    private static final String FACILITY               = "SSP30";
    private static final double GMV_MINIMO_USD         = 100.0;
    private static final double GMV_MINIMO_PROCURAR    = 350.0;
    private static final double GMV_MINIMO_OW          = 500.0;
    private static final double GMV_ALERTA_USD         = 1000.0;

    // ====================================================
    // RECORD — representa um pacote (equivalente a uma
    // linha do DataFrame pandas)
    //
    // 'record' é a forma moderna do Java (Java 16+) de
    // criar um objeto imutável de dados — similar a
    // dataclasses do Python.
    // ====================================================

    /**
     * Representa um pacote em risco (ON ROUTE ou ON WAY).
     */
    public record Pacote(
        String shipmentId,
        String situation,
        double gmvUsd,
        String lgStatus,
        String lgSubStatus,
        String dataEntrada
    ) {
        /** Retorna true se é Possível Lost de alto valor. */
        public boolean isAlertaUrgente() {
            return "Possivel Lost".equals(situation) && gmvUsd >= GMV_ALERTA_USD;
        }
    }

    /**
     * Estatísticas agregadas de uma aba (ON ROUTE ou ON WAY).
     * Equivalente ao dict retornado por atualizar_aba() no Python.
     */
    public record StatsAba(
        int total,
        int novos,
        int removidos,
        int recuperados,
        double gmvTotal,
        Map<String, Integer> porSituation,
        String topId,
        double topGmv,
        List<Pacote> novosCriticos
    ) {}

    // ====================================================
    // MÉTODOS PRINCIPAIS
    // Em Java: tipos de retorno declarados, sem duck-typing
    // ====================================================

    /**
     * Ponto de entrada do programa.
     * Equivalente ao bloco if __name__ == '__main__': no Python.
     */
    public static void main(String[] args) {
        var inicio = LocalDateTime.now();
        var fmt    = DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm");

        System.out.println("=".repeat(55));
        System.out.printf("Atualização de Risco SSP30 — %s%n", inicio.format(fmt));
        System.out.println("=".repeat(55));

        // TODO semanas 3-4: conectar ao BigQuery e Google Sheets
        // BigQueryOptions bq = BigQueryOptions.getDefaultInstance();
        // Sheets sheets = SheetsServiceUtil.getSheetsService();

        System.out.println("\n[STUB] Buscando pacotes ON ROUTE no BigQuery...");
        List<Pacote> pacotesRoute = buscarOnRoute();

        System.out.println("[STUB] Buscando pacotes ON WAY no BigQuery...");
        List<Pacote> pacotesWay   = buscarOnWay();

        StatsAba statsRoute = calcularStats(pacotesRoute);
        StatsAba statsWay   = calcularStats(pacotesWay);

        verificarAlertasUrgentes(statsRoute, statsWay);
        salvarSnapshot(statsRoute, statsWay);

        String analiseIa = gerarAnaliseIA(statsRoute, statsWay);
        enviarReportGoogleChat(statsRoute, statsWay, analiseIa, inicio, fmt);

        System.out.printf("%nConcluído | ON ROUTE: +%d | ON WAY: +%d%n",
            statsRoute.novos(), statsWay.novos());
    }

    // ====================================================
    // STUBS — implementar nas próximas semanas
    // ====================================================

    /** Semana 3-4: conectar ao BigQuery e executar QUERY_ON_ROUTE. */
    private static List<Pacote> buscarOnRoute() {
        System.out.println("  [TODO] Executar QUERY_ON_ROUTE no BigQuery");
        return List.of(); // lista vazia por enquanto
    }

    /** Semana 3-4: conectar ao BigQuery e executar QUERY_ON_WAY. */
    private static List<Pacote> buscarOnWay() {
        System.out.println("  [TODO] Executar QUERY_ON_WAY no BigQuery");
        return List.of();
    }

    /**
     * Calcula estatísticas agregadas da lista de pacotes.
     *
     * <p>Em Python usamos dict simples; em Java usamos um Record
     * tipado — o compilador garante que todos os campos existem.
     */
    private static StatsAba calcularStats(List<Pacote> pacotes) {
        double gmvTotal = pacotes.stream()
            .mapToDouble(Pacote::gmvUsd)
            .sum();

        Map<String, Integer> porSit = new HashMap<>();
        for (var p : pacotes) {
            porSit.merge(p.situation(), 1, Integer::sum);
        }

        List<Pacote> criticos = pacotes.stream()
            .filter(Pacote::isAlertaUrgente)
            .toList();

        Optional<Pacote> topPacote = pacotes.stream()
            .max(Comparator.comparingDouble(Pacote::gmvUsd));

        return new StatsAba(
            pacotes.size(),
            0,      // TODO: comparar com planilha
            0,      // TODO: comparar com planilha
            0,      // TODO: verificar entregues no BQ
            gmvTotal,
            porSit,
            topPacote.map(Pacote::shipmentId).orElse("-"),
            topPacote.map(Pacote::gmvUsd).orElse(0.0),
            criticos
        );
    }

    /** Envia alerta urgente se houver novos Possível Lost com GMV >= $1.000. */
    private static void verificarAlertasUrgentes(StatsAba route, StatsAba way) {
        var todos = new ArrayList<>(route.novosCriticos());
        todos.addAll(way.novosCriticos());
        if (todos.isEmpty()) {
            System.out.println("  Sem alertas urgentes.");
            return;
        }
        for (var p : todos) {
            System.out.printf("  🚨 ALERTA: %s | $%.2f%n", p.shipmentId(), p.gmvUsd());
            // TODO: enviar webhook Google Chat
        }
    }

    /** Salva snapshot diário na aba Snapshots do Google Sheets. */
    private static void salvarSnapshot(StatsAba route, StatsAba way) {
        double gmvTotal = route.gmvTotal() + way.gmvTotal();
        System.out.printf("  [TODO] Snapshot: OTR=%d OW=%d GMV=$%.2f%n",
            route.total(), way.total(), gmvTotal);
        // TODO semana 3-4: escrever na planilha via Sheets API
    }

    /** Chama a API do Claude para gerar análise em português. */
    private static String gerarAnaliseIA(StatsAba route, StatsAba way) {
        System.out.println("  [TODO] Chamada à API Anthropic (claude-opus-4-8)");
        // TODO semana 3-4: usar Anthropic Java SDK ou HTTP client
        // https://github.com/anthropics/anthropic-sdk-java
        return "";
    }

    /** Envia o report diário para o Google Chat via webhook. */
    private static void enviarReportGoogleChat(StatsAba route, StatsAba way,
                                               String analiseIa,
                                               LocalDateTime data,
                                               DateTimeFormatter fmt) {
        String msg = String.format(
            "*🔔 Report Risco SSP30 — %s*\n" +
            "*📦 ON ROUTE* — %d pacotes | GMV $%.2f\n" +
            "*🚛 ON WAY* — %d pacotes | GMV $%.2f\n" +
            "%s",
            data.format(fmt),
            route.total(), route.gmvTotal(),
            way.total(),   way.gmvTotal(),
            analiseIa.isEmpty() ? "" : "\n🤖 " + analiseIa
        );
        System.out.println("\n--- Report que seria enviado ao Chat ---");
        System.out.println(msg);
        // TODO semana 3-4: enviar via HttpClient para WEBHOOK_GCHAT
    }
}
