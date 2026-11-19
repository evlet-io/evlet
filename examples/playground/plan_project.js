// The Plan project prompt's script (docs/model-scripts.md): prompts.py names its methods on the tasks' hours.

export default class PlanProject extends evlet.Model {
  hours_look(field) {
    return Number(field.value) > 40 ? { tone: 'warning' } : null;
  }

  hours_tip(field) {
    return Number(field.value) > 40 ? 'More than a working week: think about splitting this task' : '';
  }
}
