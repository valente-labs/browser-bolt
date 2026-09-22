async function qwerebrasRun(tab, arm, token, task = 'note') {
  const noteGoal = 'Create and save a travel note titled Lisbon weekend. Write one short sentence suggesting a relaxed weekend with a tram ride and pastries. Stop when the note is saved.';
  const goal = task === 'choice' ? 'Select Express delivery and save the preference. Stop when the saved preference is visible.' : noteGoal + (task === 'review' ? ' Complete the review and confirmation steps.' : '');
  await tab.goto('http://127.0.0.1:18774/?task=' + task);
  const history = [], trace = [];
  const start = performance.now();
  for (let step = 0; step < 10; step++) {
    const state = await tab.playwright.evaluate(() => ({
      url: location.href, title: document.title, text: document.body.innerText,
      actions: Array.from(document.querySelectorAll('input,textarea,button')).map((e, i) => ({
        id: e.id, node: i + 1, label: e.labels?.[0]?.textContent?.trim() || e.textContent.trim(),
        kind: e.tagName === 'BUTTON' || e.type === 'radio' ? 'click' : 'fill',
        role: e.type === 'radio' ? 'radio' : e.tagName === 'BUTTON' ? 'button' : 'textbox', checked: e.checked,
        value: e.value || ''
      }))
    }));
    const response = await fetch('http://127.0.0.1:18774/decide', {
      method: 'POST', headers: {'X-Demo-Token': token}, body: JSON.stringify({arm, state, goal, history})
    });
    const result = await response.json(); trace.push(result);
    if (result.error) break;
    const d = result.decision;
    if (d.operation === 'DONE' || d.operation === 'BLOCKED') break;
    const a = state.actions.find(a => a.id === d.choice);
    if (!a) throw Error('Unobserved target');
    const before = await tab.playwright.domSnapshot();
    if (d.operation === 'TYPE_TEXT' && a.kind === 'fill' && typeof result.text === 'string') {
      await tab.playwright.getByRole('textbox', {name: a.label, exact: true}).fill(result.text);
    } else if (d.operation === 'CLICK' && a.kind === 'click') {
      await tab.playwright.getByRole(a.role, {name: a.label, exact: true}).click();
    } else throw Error('Unsupported fixture action');
    const after = await tab.playwright.domSnapshot();
    history.push({action: a.label, kind: a.kind, text: result.text || '', page_changed: before !== after});
  }
  const final = await tab.playwright.domSnapshot();
  return {arm, task, elapsed_ms: performance.now() - start, trace, final,
    success: (task === 'choice' ? final.includes('heading "Preference saved"') && final.includes('Express') : final.includes('heading "Note saved"') && final.includes('Lisbon weekend') &&
      /tram/i.test(final) && /pastr(?:y|ies)/i.test(final)) && trace.at(-1)?.decision?.operation === 'DONE'};
}
