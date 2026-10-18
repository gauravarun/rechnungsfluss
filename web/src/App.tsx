/**
 * Review UI shell. The review queue, invoice detail and approve/correct/reject
 * flows are built in W5, against the review API (ADR-0012).
 */
export function App() {
  return (
    <main>
      <header>
        <h1>Rechnungsfluss</h1>
        <p>Rechnungsprüfung · Nordwerk Industriebedarf GmbH (fiktiv)</p>
      </header>
      <section aria-labelledby="queue-heading">
        <h2 id="queue-heading">Prüfwarteschlange</h2>
        <p role="status">Noch nicht verbunden: die Review-API wird in Woche 5 gebaut.</p>
      </section>
    </main>
  );
}
