# typed: strict
# frozen_string_literal: true

# Homebrew formula for the codu session and data browser.
class Codu < Formula
  include Language::Python::Virtualenv

  desc "Browse local Codex sessions and data in an ncdu-style TUI"
  homepage "https://github.com/ActivationEnergy/codu"
  url "https://raw.githubusercontent.com/ActivationEnergy/codu/v0.3.5/codu.py"
  version "0.3.5"
  sha256 "dc315ddcc903edef3ba5e963c84aa049c7a2e70b626c1dd2427ba10cabe28e59"
  license "MIT"

  depends_on "python@3.14"

  resource "websocket-client" do
    url "https://files.pythonhosted.org/packages/d5/d2/cc4dc1271e464942db7ee278baae2daa99ee77cb2af744025c04da585a3e/websocket_client-1.9.2-py3-none-any.whl"
    sha256 "e1a673830a9c7bfa47b1cd3d5e4178f4c9651d80a4eab02c9c23a1c3ec6250ce"
  end

  def install
    venv = virtualenv_create(libexec, "python3.14", system_site_packages: false)
    venv.pip_install resource("websocket-client")
    inreplace "codu.py", "#!/usr/bin/env python3", "#!#{libexec}/bin/python"
    bin.install "codu.py" => "codu"
  end

  test do
    system libexec/"bin/python", "-c", "from websocket import create_connection"
    assert_match "codu 0.3.5", shell_output("#{bin}/codu --version")
    assert_match "usage: codu", shell_output("#{bin}/codu --help")
  end
end
