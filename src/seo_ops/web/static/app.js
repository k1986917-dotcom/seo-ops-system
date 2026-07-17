document.querySelectorAll('input[type="file"]').forEach((input) => {
  input.addEventListener('change', () => {
    const label = input.closest('.file-drop')?.querySelector('.file-name');
    if (!label) return;
    const names = Array.from(input.files || []).map((file) => file.name);
    label.textContent = names.length ? names.join('、') : '尚未选择文件';
  });
});

document.querySelectorAll('.decision-form').forEach((form) => {
  form.addEventListener('submit', (event) => {
    const submitter = event.submitter;
    if (submitter?.value === 'rejected' && !form.querySelector('input[name="reason"]')?.value.trim()) {
      if (!window.confirm('没有填写拒绝原因，系统将无法从这次决定中获得有效信息。仍要继续吗？')) {
        event.preventDefault();
      }
    }
  });
});


document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.dataset.confirm || '确认继续吗？')) {
      event.preventDefault();
    }
  });
});

document.querySelectorAll('form[data-working-label]').forEach((form) => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"]');
    if (!button) return;
    button.disabled = true;
    button.textContent = form.dataset.workingLabel || '处理中…';
  });
});

