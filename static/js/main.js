// Toggle password visibility
function togglePassword(fieldId) {
  const field = document.getElementById(fieldId);
  const eye = document.getElementById('eye-' + fieldId);
  if (!field) return;
  if (field.type === 'password') {
    field.type = 'text';
    if (eye) eye.textContent = '🙈';
  } else {
    field.type = 'password';
    if (eye) eye.textContent = '👁';
  }
}

// Auto-dismiss flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', function () {
  const flashes = document.querySelectorAll('.flash');
  flashes.forEach(function (flash) {
    setTimeout(function () {
      flash.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
      flash.style.opacity = '0';
      flash.style.transform = 'translateY(-8px)';
      setTimeout(function () { flash.remove(); }, 400);
    }, 5000);
  });

  // Highlight active nav links based on current path
  const path = window.location.pathname;
  document.querySelectorAll('.nav-link').forEach(function (link) {
    if (link.getAttribute('href') === path) {
      link.classList.add('active');
    }
  });

  // Animate stat numbers
  document.querySelectorAll('.stat-card-value').forEach(function (el) {
    const text = el.textContent.trim();
    const num = parseFloat(text.replace(/[^0-9.]/g, ''));
    if (!isNaN(num) && num > 0) {
      let start = 0;
      const duration = 1200;
      const step = 16;
      const increment = num / (duration / step);
      const prefix = text.match(/^\D*/)[0];
      const suffix = text.match(/\D*$/)[0];
      const isDecimal = text.includes('.');
      const timer = setInterval(function () {
        start += increment;
        if (start >= num) {
          start = num;
          clearInterval(timer);
        }
        el.textContent = prefix + (isDecimal ? start.toFixed(2) : Math.floor(start)) + suffix;
      }, step);
    }
  });

  // Course card micro-interactions
  document.querySelectorAll('.course-card').forEach(function (card) {
    card.addEventListener('mouseenter', function () {
      this.style.willChange = 'transform';
    });
    card.addEventListener('mouseleave', function () {
      this.style.willChange = '';
    });
  });
});
