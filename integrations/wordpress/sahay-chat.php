<?php
/**
 * Plugin Name: Sahay Chat
 * Description: Your business's AI sales & support chat (Sahay) on your WordPress / WooCommerce site.
 * Version: 0.1.0
 */
if (!defined('ABSPATH')) exit;

add_action('admin_menu', function () {
    add_options_page('Sahay Chat', 'Sahay Chat', 'manage_options', 'sahay-chat', function () { ?>
        <div class="wrap"><h1>Sahay Chat</h1>
        <form method="post" action="options.php"><?php settings_fields('sahay_chat'); ?>
        <table class="form-table">
          <tr><th>Site key (pk_…)</th><td><input class="regular-text" name="sahay_site_key" value="<?php echo esc_attr(get_option('sahay_site_key')); ?>"></td></tr>
          <tr><th>Secret key (sk_…)</th><td><input class="regular-text" type="password" name="sahay_secret_key" value="<?php echo esc_attr(get_option('sahay_secret_key')); ?>">
            <p class="description">Optional. Signs logged-in customers so the chat can show their orders. Never sent to the browser.</p></td></tr>
          <tr><th>Sahay host</th><td><input class="regular-text" name="sahay_host" value="<?php echo esc_attr(get_option('sahay_host', 'https://sahay-536440059442.asia-south1.run.app')); ?>"></td></tr>
        </table><?php submit_button(); ?></form></div>
    <?php });
});
add_action('admin_init', function () {
    foreach (['sahay_site_key', 'sahay_secret_key', 'sahay_host'] as $o) register_setting('sahay_chat', $o);
});

add_action('wp_footer', function () {
    $key = get_option('sahay_site_key');
    if (!$key) return;
    $host = rtrim(get_option('sahay_host', 'https://sahay-536440059442.asia-south1.run.app'), '/');
    $opts = ['siteKey' => $key, 'context' => new stdClass()];
    $user = wp_get_current_user();
    $secret = get_option('sahay_secret_key');
    if ($user && $user->ID && $secret) {
        $opts['user'] = ['email' => $user->user_email, 'name' => $user->display_name];
        $opts['userHash'] = hash_hmac('sha256', strtolower($user->user_email), $secret);
    }
    if (function_exists('is_product') && is_product()) {
        $p = wc_get_product(get_the_ID());
        if ($p) $opts['context'] = ['productSku' => $p->get_sku(), 'productTitle' => $p->get_name()];
    }
    echo '<script>window.Sahay=window.Sahay||function(){(window.Sahay.q=window.Sahay.q||[]).push(arguments)};Sahay("init",' . wp_json_encode($opts) . ');</script>';
    echo '<script src="' . esc_url($host . '/widget.js') . '" data-manual async></script>';
});
