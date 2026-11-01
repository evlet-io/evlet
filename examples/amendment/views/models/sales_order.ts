export const extension = {
  name: 'planning_help',
  target: 'amendment.SalesOrder',
  render(context: {notify: (message: string) => void}) {
    return {
      title: 'Production planning',
      content: 'Save the order, then choose Create Work Orders. Existing commitments reduce the available quantity.',
      actions: [{label: 'About this example', onSelect: () => context.notify('This example demonstrates document operations. It is not an inventory or manufacturing system.')}],
    };
  },
};
