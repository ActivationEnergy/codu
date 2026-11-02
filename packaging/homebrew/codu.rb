# typed: strict
# frozen_string_literal: true

# Homebrew formula for the codu session and data browser.
class Codu < Formula
  desc "Browse local Codex sessions and data in an ncdu-style TUI"
  homepage "https://github.com/ActivationEnergy/codu"
  url "https://raw.githubusercontent.com/ActivationEnergy/codu/v0.3.1/codu.py"
  version "0.3.1"
  sha256 "7e88de30065596e16427b5b444708c6abc73046cc446d0fcee3d764f545ad093"
  license "MIT"

  depends_on "python@3.14"

  def install
    inreplace "codu.py", "#!/usr/bin/env python3", "#!#{formula_opt_bin("python@3.14")}/python3.14"
    bin.install "codu.py" => "codu"
  end

  test do
    assert_match "codu 0.3.1", shell_output("#{bin}/codu --version")
    assert_match "usage: codu", shell_output("#{bin}/codu --help")
  end
end
