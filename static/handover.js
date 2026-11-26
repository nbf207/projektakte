const initHandoverPrint = () => {
  const btn = document.querySelector('[data-print-report]');
  if (!btn || btn.dataset.printBound) return;
  btn.dataset.printBound = '1';
  btn.addEventListener('click', () => window.print());
};
document.addEventListener('DOMContentLoaded', initHandoverPrint);
document.addEventListener('lg:navigate', initHandoverPrint);
if (document.readyState !== 'loading') initHandoverPrint();
