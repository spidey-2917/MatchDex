(function($) {
    $(document).ready(function() {
        var toggleField = function() {
            var cardType = $('#id_card_type').val();
            if (cardType === 'PREMIUM') {
                $('.field-premium_type').show();
            } else {
                $('.field-premium_type').hide();
                $('#id_premium_type').val(''); // Clear the value if not premium
            }
        };

        // Run on load
        toggleField();

        // Run on change
        $('#id_card_type').change(toggleField);
    });
})(django.jQuery);
