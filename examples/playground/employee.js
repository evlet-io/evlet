// The Employee model script (docs/model-scripts.md). models.py names each method: the name tooltip, email on_change,
// the Skills level look and control, the Profile section and Meta.on_open. Plain JavaScript, served as it is.

const LOOKS = { expert: 'success', learning: 'muted' };
const LEVELS = ['learning', 'practised', 'expert'];
const STYLE = 'button{font:inherit;font-size:12px;padding:1px 6px;margin-right:2px;border:1px solid var(--border);border-radius:4px;'
  + 'background:var(--surface);color:var(--fg-2);cursor:pointer}button[aria-pressed=true]{background:var(--accent-soft);color:var(--fg)}';

export default class Employee extends evlet.Model {
  opened() {
    if (this.is_new) this.ui.toast('Add the new employee’s skills below.');
  }

  name_tip(field) {
    return { title: field.value || 'New employee', lines: [`Department: ${this.field('department').value || 'none yet'}`] };
  }

  email_changed(field) {
    const email = String(field.value ?? '');
    field.warn(email && !email.includes('@') ? 'This does not look like an email address' : '');
  }

  level_look(field) {
    return LOOKS[field.value] ? { tone: LOOKS[field.value] } : null;
  }

  // The level cell as three buttons; a click sets it as if it were picked from the list.
  level_picker(el, field) {
    const draw = () => {
      const style = document.createElement('style');
      style.textContent = STYLE;
      el.replaceChildren(style, ...LEVELS.map(level => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = level;
        button.disabled = !field.editable;
        button.setAttribute('aria-pressed', String(field.value === level));
        button.addEventListener('click', () => { field.set(level); draw(); });
        return button;
      }));
    };
    draw();
  }

  profile(el) {
    const skills = this.table('skills').rows.length;
    el.textContent = `${this.field('full_name').value || 'This employee'} has ${skills} skill${skills === 1 ? '' : 's'} on record.`;
  }
}
