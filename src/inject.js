// geo-fix: Browser API override payload
// Injected by mitmproxy addon into HTML responses from target domains.
// Tokens replaced by proxy at injection time.
(function() {
    'use strict';

    // === Configuration (replaced by proxy addon) ===
    var GF_TIMEZONE = '__GF_TIMEZONE__';
    var GF_LAT = parseFloat('__GF_LAT__');
    var GF_LON = parseFloat('__GF_LON__');
    var GF_LANG = '__GF_LANG__';
    var GF_LANGS = '__GF_LANGS__'.split(',');

    // === Utility: make override non-detectable ===
    function stealthDefine(obj, prop, descriptor) {
        try {
            Object.defineProperty(obj, prop, Object.assign({
                configurable: false,
                enumerable: true
            }, descriptor));
        } catch(e) { /* already defined or frozen */ }
    }

    function stealthDefineGetter(obj, prop, getter) {
        stealthDefine(obj, prop, { get: getter });
    }

    // Disguise overridden function as native
    function disguiseFunction(fn, name) {
        try {
            Object.defineProperty(fn, 'toString', {
                value: function() { return 'function ' + name + '() { [native code] }'; },
                configurable: false,
                enumerable: false,
                writable: false
            });
        } catch(e) {}
        return fn;
    }

    // === 1. Timezone Override ===

    // Compute correct offset for the configured timezone using Intl
    function getTimezoneOffset() {
        try {
            // Create a date formatter for the target timezone
            var fmt = new Intl.DateTimeFormat('en-US', {
                timeZone: GF_TIMEZONE,
                year: 'numeric', month: 'numeric', day: 'numeric',
                hour: 'numeric', minute: 'numeric', second: 'numeric',
                hour12: false
            });
            var now = new Date();
            var parts = fmt.formatToParts(now);
            var vals = {};
            for (var i = 0; i < parts.length; i++) {
                vals[parts[i].type] = parseInt(parts[i].value, 10);
            }
            // Reconstruct as UTC and compare
            var targetTime = new Date(Date.UTC(
                vals.year, vals.month - 1, vals.day,
                vals.hour === 24 ? 0 : vals.hour, vals.minute, vals.second
            ));
            // Offset = UTC - local time in minutes
            var diff = (now.getTime() - targetTime.getTime()) / 60000;
            return Math.round(diff);
        } catch(e) {
            return 300; // Fallback to EST
        }
    }

    var cachedOffset = null;
    var cachedOffsetTime = 0;

    var origGetTimezoneOffset = Date.prototype.getTimezoneOffset;
    Date.prototype.getTimezoneOffset = disguiseFunction(function() {
        // Cache offset for 60 seconds (DST doesn't change that fast)
        var now = Date.now();
        if (cachedOffset === null || (now - cachedOffsetTime) > 60000) {
            cachedOffset = getTimezoneOffset();
            cachedOffsetTime = now;
        }
        return cachedOffset;
    }, 'getTimezoneOffset');

    // Override Intl.DateTimeFormat to inject default timezone
    var OrigDateTimeFormat = Intl.DateTimeFormat;
    var NewDateTimeFormat = disguiseFunction(function(locales, options) {
        var hasExplicitTz = options && options.timeZone;
        options = Object.assign({}, options);
        if (!options.timeZone) {
            options.timeZone = GF_TIMEZONE;
        }
        var instance = new OrigDateTimeFormat(locales, options);
        instance._gf_explicit_tz = !!hasExplicitTz;
        return instance;
    }, 'DateTimeFormat');

    // Preserve static methods and prototype
    NewDateTimeFormat.prototype = OrigDateTimeFormat.prototype;
    NewDateTimeFormat.supportedLocalesOf = OrigDateTimeFormat.supportedLocalesOf;

    try {
        Object.defineProperty(Intl, 'DateTimeFormat', {
            value: NewDateTimeFormat,
            writable: false,
            configurable: false,
            enumerable: true
        });
    } catch(e) {}

    // Override resolvedOptions to return our timezone
    var origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = disguiseFunction(function() {
        var result = origResolvedOptions.call(this);
        // Only override if no explicit timezone was set by the caller
        if (result && !this._gf_explicit_tz) {
            result.timeZone = GF_TIMEZONE;
        }
        return result;
    }, 'resolvedOptions');

    // Temporal API override (Chrome 145+)
    if (typeof Temporal !== 'undefined' && Temporal.Now) {
        try {
            var origTimeZoneId = Temporal.Now.timeZoneId;
            stealthDefine(Temporal.Now, 'timeZoneId', {
                value: disguiseFunction(function() {
                    return GF_TIMEZONE;
                }, 'timeZoneId')
            });
        } catch(e) {}

        try {
            var origTimeZone = Temporal.Now.timeZone;
            stealthDefine(Temporal.Now, 'timeZone', {
                value: disguiseFunction(function() {
                    return GF_TIMEZONE;
                }, 'timeZone')
            });
        } catch(e) {}
    }

    // === 2. Geolocation Override ===

    var fakePosition = {
        coords: {
            latitude: GF_LAT,
            longitude: GF_LON,
            accuracy: 50,
            altitude: null,
            altitudeAccuracy: null,
            heading: null,
            speed: null
        },
        timestamp: Date.now()
    };

    function fakeGetCurrentPosition(success, error, options) {
        if (typeof success === 'function') {
            setTimeout(function() {
                fakePosition.timestamp = Date.now();
                success(fakePosition);
            }, 50 + Math.random() * 100); // Simulate realistic delay
        }
    }

    function fakeWatchPosition(success, error, options) {
        if (typeof success === 'function') {
            var id = setInterval(function() {
                fakePosition.timestamp = Date.now();
                success(fakePosition);
            }, 3000);
            return id;
        }
        return 0;
    }

    function fakeClearWatch(id) {
        clearInterval(id);
    }

    if (navigator.geolocation) {
        stealthDefine(navigator.geolocation, 'getCurrentPosition', {
            value: disguiseFunction(fakeGetCurrentPosition, 'getCurrentPosition'),
            writable: false
        });
        stealthDefine(navigator.geolocation, 'watchPosition', {
            value: disguiseFunction(fakeWatchPosition, 'watchPosition'),
            writable: false
        });
        stealthDefine(navigator.geolocation, 'clearWatch', {
            value: disguiseFunction(fakeClearWatch, 'clearWatch'),
            writable: false
        });
    }

    // === 3. Language Override ===

    stealthDefineGetter(Navigator.prototype, 'language', function() {
        return GF_LANG;
    });

    stealthDefineGetter(Navigator.prototype, 'languages', function() {
        return Object.freeze(GF_LANGS);
    });

    // Also override deprecated navigator.userLanguage and browserLanguage (IE/Edge legacy)
    try {
        stealthDefineGetter(Navigator.prototype, 'userLanguage', function() {
            return GF_LANG;
        });
        stealthDefineGetter(Navigator.prototype, 'browserLanguage', function() {
            return GF_LANG;
        });
    } catch(e) {}

    // === 4. WebRTC Leak Prevention ===

    // Wrap RTCPeerConnection to block STUN server discovery
    var OrigRTCPeerConnection = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;

    if (OrigRTCPeerConnection) {
        var blockedStunPattern = /^stun:|^turn:/i;

        function FilteredRTCPeerConnection(config, constraints) {
            // Remove or neutralize STUN/TURN servers from ICE configuration
            if (config && config.iceServers) {
                config = JSON.parse(JSON.stringify(config)); // deep clone
                config.iceServers = config.iceServers.filter(function(server) {
                    var urls = server.urls || server.url || [];
                    if (typeof urls === 'string') urls = [urls];
                    // Block any server with stun: or turn: URLs
                    for (var i = 0; i < urls.length; i++) {
                        if (blockedStunPattern.test(urls[i])) {
                            return false;
                        }
                    }
                    return true;
                });
            }
            return new OrigRTCPeerConnection(config, constraints);
        }

        FilteredRTCPeerConnection.prototype = OrigRTCPeerConnection.prototype;
        FilteredRTCPeerConnection.generateCertificate = OrigRTCPeerConnection.generateCertificate;

        disguiseFunction(FilteredRTCPeerConnection, 'RTCPeerConnection');

        stealthDefine(window, 'RTCPeerConnection', {
            value: FilteredRTCPeerConnection,
            writable: false
        });

        // Also handle webkit prefix
        if (window.webkitRTCPeerConnection) {
            stealthDefine(window, 'webkitRTCPeerConnection', {
                value: FilteredRTCPeerConnection,
                writable: false
            });
        }
    }
})();
