#!/usr/bin/env bash
# ===========================================================
# crypto.sh — wrapper centralizado de cifrado para archive.sh
# y db_archive.sh. Tres modos:
#   - age (opt-in via ARCHIVE_AGE_RECIPIENTS) — public-key crypto
#   - openssl (legacy via ARCHIVE_PASSWORD)   — password-based
#   - none (passthrough, sin cifrar)
#
# Restore auto-detecta por extensión del path (.age o .enc).
# ===========================================================

# crypto_mode: imprime "age" | "openssl" | "none"
crypto_mode() {
    if [[ -n "${ARCHIVE_AGE_RECIPIENTS:-}" ]]; then
        if [[ -n "${ARCHIVE_PASSWORD:-}" ]]; then
            echo "warning: ARCHIVE_AGE_RECIPIENTS y ARCHIVE_PASSWORD ambos seteados; age tiene precedencia" >&2
        fi
        echo "age"
        return
    fi
    if [[ -n "${ARCHIVE_PASSWORD:-}" ]]; then
        echo "openssl"
        return
    fi
    echo "none"
}

# crypto_extension: imprime el sufijo (sin punto) que se appendea
# al filename del archivo cifrado. "" para passthrough.
crypto_extension() {
    case "$(crypto_mode)" in
        age)     echo "age" ;;
        openssl) echo "enc" ;;
        *)       echo ""    ;;
    esac
}

# crypto_encrypt_pipe: lee stdin, escribe a stdout cifrado según el modo.
# Diseñado para usarse en pipelines: `... | crypto_encrypt_pipe | rclone rcat`.
crypto_encrypt_pipe() {
    case "$(crypto_mode)" in
        age)
            local args=()
            for r in $ARCHIVE_AGE_RECIPIENTS; do
                args+=(-r "$r")
            done
            exec age "${args[@]}"
            ;;
        openssl)
            exec openssl enc -aes-256-cbc -pbkdf2 -iter 100000 \
                -pass env:ARCHIVE_PASSWORD
            ;;
        *)
            exec cat
            ;;
    esac
}

# crypto_decrypt_for_path: detecta por extensión y descifra.
# Uso: `rclone cat path | crypto_decrypt_for_path "$path" | zstd -dc | tar -xf -`
crypto_decrypt_for_path() {
    local path="$1"
    if [[ "$path" == *.age ]]; then
        local id="${ARCHIVE_AGE_IDENTITY_FILE:-}"
        [[ -n "$id" && -f "$id" ]] || die "Restore .age requiere ARCHIVE_AGE_IDENTITY_FILE apuntando a la privada"
        exec age -d -i "$id"
    elif [[ "$path" == *.enc ]]; then
        [[ -n "${ARCHIVE_PASSWORD:-}" ]] || die "Restore .enc requiere ARCHIVE_PASSWORD"
        exec openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
            -pass env:ARCHIVE_PASSWORD
    else
        exec cat
    fi
}
