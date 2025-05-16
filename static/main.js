document.addEventListener('DOMContentLoaded', function() {
    const authButtons = document.querySelectorAll('.redirect-to-auth');

    authButtons.forEach(button => {
        button.addEventListener('click', function() {
            const authUrl = this.getAttribute('data-auth-url');
            if (authUrl) {
                window.location.href = authUrl;
            }
        });
    });
});

const showPasswordCheckbox = document.getElementById('show_password');
const passwordInput = document.getElementById('password');
const showConfirmPasswordCheckbox = document.getElementById('show_confirm_password');
const confirmPasswordInput = document.getElementById('confirm_password');

if (showPasswordCheckbox && passwordInput) {
    showPasswordCheckbox.addEventListener('change', function() {
        passwordInput.type = this.checked ? 'text' : 'password';
    });
}

if (showConfirmPasswordCheckbox && confirmPasswordInput) {
    showConfirmPasswordCheckbox.addEventListener('change', function() {
        confirmPasswordInput.type = this.checked ? 'text' : 'password';
    });
}

document.addEventListener('DOMContentLoaded', function() {
    const userInfoDiv = document.getElementById('userInfo');

    if (userInfoDiv) {
        userInfoDiv.addEventListener('click', function() {
            const userProfileUrl = this.getAttribute('data-user-url');
            if (userProfileUrl) {
                window.location.href = userProfileUrl;
            }
        });

        userInfoDiv.style.cursor = 'pointer';
    }
});

document.addEventListener('DOMContentLoaded', function() {
    const logoTitle = document.getElementById('logoTitle');

    if (logoTitle) {
        logoTitle.addEventListener('click', function() {
            const indexUrl = this.getAttribute('data-url');
            if (indexUrl) {
                window.location.href = indexUrl;
            }
        });

        logoTitle.style.cursor = 'pointer';
    }
});